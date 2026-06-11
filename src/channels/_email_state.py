"""Persistent state for deadsimple email polling.

Two concerns live here, deliberately decoupled:

1. **Discovery cursor** — a (created_at, message_id) tuple marking the
   newest inbound we have *seen*. created_at alone is insufficient because
   deadsimple may emit two messages with identical timestamps, in which case
   message_id lexicographic order breaks the tie. The cursor advances at poll
   so pagination stays cheap.

2. **Pending-reply ledger** — every enqueued-but-unanswered inbound, keyed by
   its message_id. Because the cursor advances at poll (long before a reply is
   actually sent), the ledger is what guarantees no message is silently
   dropped: a failed send leaves a durable, retryable record, and a restart
   re-drives it. An inbound is only "done" once its reply is acked.

The file also distinguishes a genuine first run (file absent) from a corrupt
state file (garbage JSON): the former may fast-forward past pre-existing mail,
the latter must NOT — it should alert an operator instead of silently skipping.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Cap retained dead-letter records so the state file can't grow unbounded.
MAX_DEAD_RECORDS = 50


def _parse_dt(s: Any) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


@dataclass
class PendingReply:
    """A durable record of an enqueued inbound awaiting a confirmed reply.

    status transitions: queued -> retry -> dead.
      - queued: handed to the agent; reply not yet attempted (or in flight).
      - retry:  a send failed; `reply_payload` holds the computed reply so the
                drain loop can re-send it without re-running the agent.
      - dead:   exceeded the retry budget; surfaced to an operator.
    """

    created_at: datetime | None = None
    thread_id: str = ""
    reply_address: dict = field(default_factory=dict)
    status: str = "queued"
    attempts: int = 0
    next_retry_at: datetime | None = None
    reply_payload: dict | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "thread_id": self.thread_id,
            "reply_address": self.reply_address,
            "status": self.status,
            "attempts": self.attempts,
            "next_retry_at": self.next_retry_at.isoformat() if self.next_retry_at else None,
            "reply_payload": self.reply_payload,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PendingReply":
        return cls(
            created_at=_parse_dt(d.get("created_at")),
            thread_id=d.get("thread_id", "") or "",
            reply_address=d.get("reply_address") or {},
            status=d.get("status", "queued") or "queued",
            attempts=int(d.get("attempts") or 0),
            next_retry_at=_parse_dt(d.get("next_retry_at")),
            reply_payload=d.get("reply_payload"),
            last_error=d.get("last_error"),
        )


@dataclass
class EmailState:
    path: Path
    cursor_created_at: datetime | None = None
    cursor_message_id: str = ""
    pending: dict[str, PendingReply] = field(default_factory=dict)
    # True iff the on-disk file existed but could not be parsed. A genuine
    # first run (file absent) leaves this False.
    corrupt: bool = False

    # --- Back-compat aliases (legacy watermark_* in-memory API) ----------

    @property
    def watermark_created_at(self) -> datetime | None:
        return self.cursor_created_at

    @property
    def watermark_message_id(self) -> str:
        return self.cursor_message_id

    def set_watermark(self, created_at: datetime, message_id: str) -> None:
        self.set_cursor(created_at, message_id)

    def is_after_watermark(self, created_at: datetime, message_id: str) -> bool:
        return self.is_after_cursor(created_at, message_id)

    # --- Load / save -----------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "EmailState":
        """Load state from disk.

        A missing file yields a blank, non-corrupt state (genuine first run).
        A present-but-unparseable file yields a blank state flagged
        `corrupt=True` so the caller can alert instead of fast-forwarding.
        Accepts both the new `cursor_*` keys and the legacy `watermark_*` keys.
        """
        state = cls(path=path)
        try:
            text = path.read_text()
        except FileNotFoundError:
            return state  # genuine first run
        try:
            raw = json.loads(text)
            if not isinstance(raw, dict):
                raise ValueError("state file is not a JSON object")
            ts = raw.get("cursor_created_at") or raw.get("watermark_created_at")
            if ts:
                state.cursor_created_at = _parse_dt(ts)
            state.cursor_message_id = (
                raw.get("cursor_message_id") or raw.get("watermark_message_id") or ""
            )
            pending = raw.get("pending") or {}
            for mid, rec in pending.items():
                try:
                    state.pending[mid] = PendingReply.from_dict(rec)
                except (TypeError, ValueError):
                    continue
        except (json.JSONDecodeError, ValueError, TypeError):
            # Reset to blank but flag so the caller does NOT fast-forward.
            state.cursor_created_at = None
            state.cursor_message_id = ""
            state.pending = {}
            state.corrupt = True
        return state

    def set_cursor(self, created_at: datetime, message_id: str) -> None:
        self.cursor_created_at = created_at
        self.cursor_message_id = message_id

    def is_after_cursor(self, created_at: datetime, message_id: str) -> bool:
        """True iff (created_at, message_id) sorts strictly after the cursor.

        Both datetimes must be timezone-aware (the deadsimple API returns UTC
        ISO 8601). Mixing naive and aware datetimes raises TypeError at the
        comparison.
        """
        if self.cursor_created_at is None:
            return True
        return (created_at, message_id) > (self.cursor_created_at, self.cursor_message_id)

    # --- Pending-reply ledger -------------------------------------------

    def add_pending(
        self,
        message_id: str,
        *,
        created_at: datetime | None,
        thread_id: str,
        reply_address: dict,
    ) -> None:
        self.pending[message_id] = PendingReply(
            created_at=created_at,
            thread_id=thread_id,
            reply_address=dict(reply_address),
            status="queued",
        )

    def ack(self, message_id: str) -> None:
        """Reply confirmed sent — drop the ledger entry. No-op if unknown."""
        self.pending.pop(message_id, None)

    def mark_retry(
        self,
        message_id: str,
        *,
        reply_payload: dict,
        next_retry_at: datetime | None,
        error: str | None = None,
    ) -> None:
        """A send failed: persist the computed reply for a later re-send."""
        pr = self.pending.get(message_id)
        if pr is None:
            pr = PendingReply()
            self.pending[message_id] = pr
        pr.status = "retry"
        pr.reply_payload = reply_payload
        pr.attempts += 1
        pr.next_retry_at = next_retry_at
        pr.last_error = str(error) if error is not None else None

    def mark_dead(self, message_id: str) -> None:
        pr = self.pending.get(message_id)
        if pr is not None:
            pr.status = "dead"
        self._prune_dead()

    def due_retries(self, now: datetime) -> list[tuple[str, PendingReply]]:
        """Retry-status entries whose backoff window has elapsed."""
        return [
            (mid, pr)
            for mid, pr in self.pending.items()
            if pr.status == "retry"
            and (pr.next_retry_at is None or pr.next_retry_at <= now)
        ]

    def _prune_dead(self) -> None:
        dead = [(mid, pr) for mid, pr in self.pending.items() if pr.status == "dead"]
        if len(dead) <= MAX_DEAD_RECORDS:
            return
        # Drop the oldest dead records (by created_at; None sorts first).
        dead.sort(key=lambda kv: (kv[1].created_at is not None, kv[1].created_at))
        for mid, _ in dead[: len(dead) - MAX_DEAD_RECORDS]:
            self.pending.pop(mid, None)

    def save(self) -> None:
        """Atomic write via temp-file + os.replace.

        Mirrors the cursor into the legacy `watermark_*` keys so an older
        binary that still reads those keys keeps working after a rollback.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cursor_iso = (
            self.cursor_created_at.isoformat() if self.cursor_created_at else None
        )
        payload = {
            "cursor_created_at": cursor_iso,
            "cursor_message_id": self.cursor_message_id,
            # Legacy mirror — back-compat for pre-cursor readers.
            "watermark_created_at": cursor_iso,
            "watermark_message_id": self.cursor_message_id,
            "pending": {mid: pr.to_dict() for mid, pr in self.pending.items()},
        }
        fd, tmp_path = tempfile.mkstemp(dir=self.path.parent, prefix=".email_state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f)
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
