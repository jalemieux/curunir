"""Persistent state for the nudge engine.

Tracks when the user last sent a message, which re-engagement tiers have
already fired for the current idle period, and when the last weekly
proactive nudge was sent. Stored as a small JSON file alongside the
context dir so it survives container restarts.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class NudgeState:
    last_user_msg_at: float = 0.0
    tiers_sent_this_idle: list[str] = field(default_factory=list)
    last_weekly_at: float = 0.0
    _path: Path | None = field(default=None, init=False, repr=False, compare=False)

    @classmethod
    def load(cls, path: Path) -> "NudgeState":
        path = Path(path)
        if not path.exists():
            state = cls(last_user_msg_at=time.time())
            state._path = path
            state.save()
            return state
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("nudge_state %s unreadable (%s); reinitializing", path, e)
            state = cls(last_user_msg_at=time.time())
            state._path = path
            state.save()
            return state
        state = cls(
            last_user_msg_at=float(data.get("last_user_msg_at", time.time())),
            tiers_sent_this_idle=list(data.get("tiers_sent_this_idle", [])),
            last_weekly_at=float(data.get("last_weekly_at", 0.0)),
        )
        state._path = path
        return state

    def save(self) -> None:
        if self._path is None:
            raise RuntimeError("NudgeState has no path; was it constructed via load()?")
        payload = {
            "last_user_msg_at": self.last_user_msg_at,
            "tiers_sent_this_idle": self.tiers_sent_this_idle,
            "last_weekly_at": self.last_weekly_at,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent, prefix=".nudge_state.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @classmethod
    def record_user_message(cls, path: Path) -> None:
        """Called from agent_worker on every inbound. Bumps the timestamp
        and clears the ladder so the next idle period starts fresh."""
        state = cls.load(path)
        state.last_user_msg_at = time.time()
        state.tiers_sent_this_idle = []
        state.save()
