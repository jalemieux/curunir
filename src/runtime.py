"""Multi-tenant agent registry (#420).

A single process hosts N personas concurrently. Each persona gets an
``AgentRuntime`` that owns its own ``AgentConfig`` (rooted at
``context/<persona>`` so every per-persona state artifact — memory,
conversations, the SQLite stores, identity, workspace — forks cleanly) and its
own ``Agent``. A shared in/out queue pair is kept; the ``agent_worker``
dispatcher (in ``run.py``) routes each message to ``registry[msg.persona]``.

Per the #420 hard requirement, each runtime's FS-touching tools are jailed to
its ``workdir`` (``context/<persona>/workspace``). The authoritative isolation
boundary in production is a container/namespace per persona; the realpath
path-jail enforced here is the testable defense-in-depth layer inside it.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from onboarding.bootstrap import bootstrap_context
from src.agent.agent import Agent
from src.config import AgentConfig, EmailChannelConfig
from src.persona import load_persona, warn_missing_keys
from src.schedule_store import db as schedule_db
from src.usage_store import UsageStore

logger = logging.getLogger(__name__)


@dataclass
class AgentRuntime:
    """One persona's slice of the process: its config, agent, and identity."""

    persona: str
    config: AgentConfig
    agent: Agent


def parse_personas(env: Mapping[str, str], default: str = "default") -> list[str]:
    """Resolve the persona list to host.

    ``CURUNIR_PERSONAS`` (comma-separated) wins; otherwise fall back to the
    single ``CURUNIR_PERSONA`` (or ``default``). Order is preserved and
    duplicates are dropped so the first listed persona stays the registry's
    fallback target.
    """
    raw = (env.get("CURUNIR_PERSONAS") or "").strip()
    if raw:
        names = [p.strip() for p in raw.split(",") if p.strip()]
    else:
        names = [(env.get("CURUNIR_PERSONA") or "").strip() or default]
    seen: dict[str, None] = {}
    for n in names:
        seen.setdefault(n, None)
    return list(seen)


def _default_agent_factory(config: AgentConfig, usage_store) -> Agent:
    return Agent(config, usage_store=usage_store)


def _default_usage_store_factory(config: AgentConfig) -> UsageStore:
    return UsageStore(config.usage_db)


def build_registry(
    persona_names: Iterable[str],
    *,
    base_context: Path = Path("./context"),
    config_overrides: Mapping | None = None,
    agent_factory: Callable[[AgentConfig, object], object] = _default_agent_factory,
    usage_store_factory: Callable[[AgentConfig], object] = _default_usage_store_factory,
    env: Mapping[str, str] | None = None,
) -> dict[str, AgentRuntime]:
    """Build the ``{persona -> AgentRuntime}`` map.

    Each persona is keyed by its directory name (what ``CURUNIR_PERSONAS``
    lists and what messages carry). For each one we load its bundle, root an
    ``AgentConfig`` at ``base_context/<persona>`` with ``fs_jail`` on, bootstrap
    that context dir, provision the workspace + schedule store, and construct
    the agent.
    """
    overrides = dict(config_overrides or {})
    registry: dict[str, AgentRuntime] = {}

    for name in persona_names:
        if name in registry:
            continue
        persona = load_persona(name)
        if env is not None:
            warn_missing_keys(persona, env)

        context_dir = Path(base_context) / name
        config = AgentConfig(
            **overrides,
            context_dir=context_dir,
            fs_jail=True,
            persona=name,
            **({"skill_allowlist": persona.skills} if persona.skills else {}),
        )

        # Seed context/<persona>/ (non-overwriting) before anything reads it,
        # then provision the per-persona FS sandbox root and schedule store.
        bootstrap_context(context_dir)
        Path(config.workdir).mkdir(parents=True, exist_ok=True)
        schedule_db.init_db(str(config.schedules_db))

        usage_store = usage_store_factory(config)
        agent = agent_factory(config, usage_store)
        registry[name] = AgentRuntime(persona=name, config=config, agent=agent)
        logger.info(
            "Persona '%s' runtime ready: context=%s, skills=%s",
            name, context_dir,
            f"{len(persona.skills)} allowlisted" if persona.skills else "all on disk",
        )

    return registry


def _env_suffix(persona: str) -> str:
    """Env-var suffix for a persona (e.g. ``life-coach`` -> ``LIFE_COACH``)."""
    return re.sub(r"[^A-Z0-9]", "_", persona.upper())


def _persona_env(
    env: Mapping[str, str], base_key: str, persona: str, *, multi: bool
) -> str | None:
    """Resolve a per-persona env var, falling back to the global one.

    Multi-tenant deployments give each persona its own inbox credentials via
    ``<BASE_KEY>__<PERSONA>`` (e.g. ``FASTMAIL_USER__FINANCE``). In the
    single-persona case the bare ``<BASE_KEY>`` is honored for back-compat;
    when hosting many personas the bare key is NOT shared (that would point
    every persona at one inbox), so a persona without suffixed credentials is
    simply skipped.
    """
    suffixed = env.get(f"{base_key}__{_env_suffix(persona)}")
    if suffixed not in (None, ""):
        return suffixed
    if not multi:
        return env.get(base_key)
    return None


def build_email_configs(
    env: Mapping[str, str], registry: Mapping[str, AgentRuntime]
) -> dict[str, EmailChannelConfig]:
    """Build ``{persona -> EmailChannelConfig}`` for personas with credentials.

    One Fastmail inbox per persona (per #420): each persona's discovery cursor
    + pending-reply ledger live under its own ``context/<persona>/`` root. A
    persona without resolvable ``FASTMAIL_USER``/``FASTMAIL_PASSWORD`` is
    omitted (no inbox provisioned for it).
    """
    multi = len(registry) > 1
    out: dict[str, EmailChannelConfig] = {}
    for persona, rt in registry.items():
        user = _persona_env(env, "FASTMAIL_USER", persona, multi=multi)
        password = _persona_env(env, "FASTMAIL_PASSWORD", persona, multi=multi)
        if not user or not password:
            continue
        inbox = (
            _persona_env(env, "FASTMAIL_INBOX", persona, multi=multi) or user
        )
        out[persona] = EmailChannelConfig(
            enabled=True,
            imap_host=env.get("FASTMAIL_IMAP_HOST", "imap.fastmail.com"),
            smtp_host=env.get("FASTMAIL_SMTP_HOST", "smtp.fastmail.com"),
            user=user,
            password=password,
            inbox=inbox,
            poll_interval_sec=int(env.get("EMAIL_POLL_INTERVAL", "60")),
            allowed_senders=[
                s.strip()
                for s in env.get("EMAIL_ALLOWED_SENDERS", "").split(",")
                if s.strip()
            ],
            restrict_outbound=env.get("EMAIL_RESTRICT_OUTBOUND", "true").lower()
            == "true",
            attachment_dir=env.get("EMAIL_ATTACHMENT_DIR", "/tmp/attachments"),
            # Per-persona discovery cursor + pending-reply ledger.
            state_file=rt.config.context_dir / "email_state.json",
            spam_score_threshold=float(env.get("EMAIL_SPAM_SCORE_THRESHOLD", "5.0")),
            send_max_retries=int(env.get("EMAIL_SEND_MAX_RETRIES", "5")),
            send_retry_backoff_sec=float(env.get("EMAIL_SEND_RETRY_BACKOFF", "30")),
            failure_alert_threshold=int(env.get("EMAIL_FAILURE_ALERT_THRESHOLD", "5")),
        )
    return out


def default_runtime(registry: Mapping[str, AgentRuntime]) -> AgentRuntime:
    """The fallback runtime for blank/unknown personas.

    Prefers an explicit ``default`` persona; otherwise the first one built
    (insertion order is preserved), which is the single runtime in the common
    single-tenant case.
    """
    if "default" in registry:
        return registry["default"]
    return next(iter(registry.values()))
