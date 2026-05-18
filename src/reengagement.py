# src/reengagement.py
"""Proactive re-engagement nudges — infrastructure the owner cannot disarm.

Two pieces live here:

1. An activity store over ``context/activity.json`` that records when the
   owner last interacted with the assistant. ``record_interaction()`` is
   called from the agent worker after a genuine owner turn.

2. ``ReengagementJob`` — a code-registered scheduler job (see
   ``src.scheduler.SYSTEM_JOBS``) that decides, deterministically, whether
   to email the owner an inactivity nudge. The LLM is consulted for exactly
   one thing: writing the email body. Whether/when to nudge, who to email,
   and the send itself are plain Python so the model cannot forget to send,
   double-send, or skip the gate.

Two nudge series, selected from account age:

    Activation     account age <= ACTIVATION_WINDOW_DAYS   thresholds 2/5/10
    Re-engagement  account age >  ACTIVATION_WINDOW_DAYS    thresholds 7/14/21

Both are inactivity-gated: an owner who messages often gets zero nudges.
``record_interaction`` re-arms the series by resetting the sent counter.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field

from src.channels.deadsimple import build_client_from_env
from src.llm import call_llm

logger = logging.getLogger(__name__)

_SECONDS_PER_DAY = 86400

# Subject lines are deterministic Python — only the body comes from the LLM.
_SERIES_SUBJECT = {
    "activation": "Getting started with your assistant",
    "reengagement": "Checking in",
}


def _parse_thresholds(raw: str, default: tuple[int, ...]) -> tuple[int, ...]:
    """Parse a comma-separated day list, falling back to ``default`` if empty
    or malformed."""
    if not raw:
        return default
    try:
        parsed = tuple(int(p.strip()) for p in raw.split(",") if p.strip())
    except ValueError:
        return default
    return parsed or default


@dataclass
class ReengagementConfig:
    """All knobs for the re-engagement feature. Every value is env-configurable."""

    enabled: bool = False
    cron: str = "0 14 * * *"
    activation_window_days: int = 14
    activation_thresholds: tuple[int, ...] = (2, 5, 10)
    reengagement_thresholds: tuple[int, ...] = (7, 14, 21)
    email_enabled: bool = False
    owner_email: str = ""

    @classmethod
    def from_env(cls) -> "ReengagementConfig":
        """Build from the process environment.

        ``owner_email`` falls back to the first ``EMAIL_ALLOWED_SENDERS``
        entry, since that is the owner in every current deployment.
        """
        owner = os.environ.get("REENGAGEMENT_OWNER_EMAIL", "").strip()
        if not owner:
            senders = [
                s.strip()
                for s in os.environ.get("EMAIL_ALLOWED_SENDERS", "").split(",")
                if s.strip()
            ]
            owner = senders[0] if senders else ""
        return cls(
            enabled=os.environ.get("REENGAGEMENT_ENABLED", "false").lower() == "true",
            cron=os.environ.get("REENGAGEMENT_CRON", "0 14 * * *"),
            activation_window_days=int(os.environ.get("ACTIVATION_WINDOW_DAYS", "14")),
            activation_thresholds=_parse_thresholds(
                os.environ.get("ACTIVATION_THRESHOLDS", ""), (2, 5, 10)
            ),
            reengagement_thresholds=_parse_thresholds(
                os.environ.get("REENGAGEMENT_THRESHOLDS", ""), (7, 14, 21)
            ),
            email_enabled=os.environ.get("EMAIL_ENABLED", "false").lower() == "true",
            owner_email=owner,
        )


# --- activity store -------------------------------------------------------


def _activity_path(config):
    return config.context_dir / "activity.json"


def load_activity(config) -> dict:
    """Read ``activity.json``. Returns ``{}`` if absent or unreadable."""
    path = _activity_path(config)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read activity.json: %s", e)
        return {}
    return data if isinstance(data, dict) else {}


def _save_activity(config, data: dict) -> None:
    """Atomically write ``data`` to ``activity.json`` (temp file + rename)."""
    path = _activity_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.rename(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record_interaction(config, now: float | None = None) -> dict:
    """Record a genuine owner interaction.

    Sets ``created_at`` on first call, updates ``last_interaction_at``, and
    re-arms the nudge series by resetting ``nudges_sent`` to 0 — an owner who
    came back doesn't need to be chased.
    """
    now = time.time() if now is None else now
    data = load_activity(config)
    if not data.get("created_at"):
        data["created_at"] = now
    data["last_interaction_at"] = now
    data["nudges_sent"] = 0
    _save_activity(config, data)
    return data


def mark_nudge_sent(config, now: float | None = None) -> dict:
    """Record that a nudge email was just sent: bump the counter, stamp the time."""
    now = time.time() if now is None else now
    data = load_activity(config)
    data["last_nudge_at"] = now
    data["nudges_sent"] = data.get("nudges_sent", 0) + 1
    _save_activity(config, data)
    return data


# --- pure decision logic --------------------------------------------------


def select_series(created_at: float, now: float, activation_window_days: int) -> str:
    """``"activation"`` for young accounts, ``"reengagement"`` for older ones."""
    age_days = (now - created_at) / _SECONDS_PER_DAY
    return "activation" if age_days <= activation_window_days else "reengagement"


def should_nudge(
    activity: dict, now: float, config: ReengagementConfig
) -> tuple[str, str | None, str]:
    """Decide whether to send a nudge. Pure — no I/O, no side effects.

    Returns ``(decision, series, reason)`` where ``decision`` is ``"GO"`` or
    ``"SKIP"`` and ``series`` is the selected series (``None`` when the gate
    fails before a series is known).
    """
    if not config.enabled:
        return ("SKIP", None, "feature disabled")
    if not config.email_enabled:
        return ("SKIP", None, "email channel disabled")
    if not config.owner_email:
        return ("SKIP", None, "no owner email configured")

    created_at = activity.get("created_at")
    last_interaction = activity.get("last_interaction_at")
    if not created_at or not last_interaction:
        return ("SKIP", None, "no activity baseline yet")

    series = select_series(created_at, now, config.activation_window_days)
    thresholds = (
        config.activation_thresholds
        if series == "activation"
        else config.reengagement_thresholds
    )
    cap = len(thresholds)
    nudges_sent = activity.get("nudges_sent", 0)
    if nudges_sent >= cap:
        return ("SKIP", series, f"nudge cap reached ({nudges_sent}/{cap})")

    quiet_days = (now - last_interaction) / _SECONDS_PER_DAY
    threshold = thresholds[nudges_sent]
    if quiet_days < threshold:
        return (
            "SKIP",
            series,
            f"quiet for {quiet_days:.1f}d, below {threshold}d threshold",
        )
    return (
        "GO",
        series,
        f"quiet for {quiet_days:.1f}d >= {threshold}d threshold "
        f"(nudge {nudges_sent + 1}/{cap})",
    )


# --- nudge composition + send --------------------------------------------


def _read_text(path) -> str:
    try:
        return path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _gather_context(agent, series: str) -> str:
    """Collect series-specific material to ground the email body.

    Activation nudges teach (what the assistant can do → skill manifest);
    re-engagement nudges restore continuity (where the owner left off →
    memory timeline). Both include the agent's identity so the email is in
    the assistant's own voice.
    """
    parts: list[str] = []
    identity = _read_text(agent.config.identity_file)
    if identity:
        parts.append("## Your identity\n" + identity)

    if series == "activation":
        from src.skills import build_skill_manifest

        manifest = build_skill_manifest(agent.config.skill_dirs)
        if manifest:
            parts.append("## Skills you can offer\n" + manifest)
    else:
        timeline = _read_text(
            agent.config.context_dir / "memory" / "summaries" / "timeline.md"
        )
        if timeline:
            parts.append("## Recent activity\n" + timeline[-4000:])
    return "\n\n".join(parts)


def _build_compose_prompt(series: str, context: str) -> str:
    if series == "activation":
        intent = (
            "The owner set you up recently but hasn't used you much. Write a "
            "short, warm email that teaches them one or two concrete things "
            "you can do for them, and invites a reply to get started. Do not "
            "be pushy."
        )
    else:
        intent = (
            "The owner has drifted away after using you before. Write a short, "
            "warm check-in email that gently reconnects — reference where they "
            "left off if the context below shows it, and invite them back. Do "
            "not be pushy or guilt-trip them."
        )
    return (
        f"{intent}\n\n"
        "Write only the plain-text email body — no subject line, no headers, "
        "no markdown. Keep it under 150 words.\n\n"
        f"Context:\n{context if context else '(no additional context available)'}"
    )


async def _compose_nudge(agent, series: str, context: str) -> tuple[str, str]:
    """Return ``(subject, body)``. Subject is fixed; body comes from the LLM."""
    prompt = _build_compose_prompt(series, context)
    resp = await call_llm(
        agent.config.model,
        [{"role": "user", "content": prompt}],
        tools=[],
        api_base=agent.config.api_base,
        openrouter_provider=agent.config.openrouter_provider,
    )
    body = (resp.text or "").strip()
    return _SERIES_SUBJECT[series], body


@dataclass
class ReengagementJob:
    """Code-registered scheduler job. Registered into ``SYSTEM_JOBS`` at
    startup so the LLM ``schedule`` tool cannot see, edit, or disable it."""

    config: ReengagementConfig
    id: str = "reengagement-nudge"
    cron: str = field(default="")

    def __post_init__(self):
        if not self.cron:
            self.cron = self.config.cron

    async def run(self, agent) -> None:
        """One scheduler tick. Gate → compose → send → mark, in that order.

        If the send raises, ``mark_nudge_sent`` is never reached, so the
        nudge is retried on the next tick (the counter is unchanged).
        """
        now = time.time()
        activity = load_activity(agent.config)
        decision, series, reason = should_nudge(activity, now, self.config)
        logger.info(
            "Re-engagement check: %s (series=%s) — %s", decision, series, reason
        )
        if decision != "GO":
            return

        context = _gather_context(agent, series)
        subject, body = await _compose_nudge(agent, series, context)
        if not body:
            logger.warning("Re-engagement nudge skipped: empty body from LLM")
            return

        client = build_client_from_env()
        try:
            await client.send_email(
                to=self.config.owner_email, subject=subject, text_body=body
            )
        finally:
            await client.aclose()

        mark_nudge_sent(agent.config, now)
        logger.info(
            "Re-engagement nudge sent to %s (%s series)",
            self.config.owner_email,
            series,
        )
