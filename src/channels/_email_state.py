"""Persistent watermark for deadsimple email polling.

The watermark is a (created_at, message_id) tuple — created_at alone is
insufficient because deadsimple may emit two messages with identical
timestamps, in which case message_id lexicographic order breaks the tie.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class EmailState:
    path: Path
    watermark_created_at: datetime | None = None
    watermark_message_id: str = ""

    @classmethod
    def load(cls, path: Path) -> "EmailState":
        """Load watermark from disk. Missing/corrupt files yield a blank state."""
        state = cls(path=path)
        try:
            raw = json.loads(path.read_text())
            ts = raw.get("watermark_created_at")
            if ts:
                state.watermark_created_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            state.watermark_message_id = raw.get("watermark_message_id", "") or ""
        except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError):
            pass
        return state

    def set_watermark(self, created_at: datetime, message_id: str) -> None:
        self.watermark_created_at = created_at
        self.watermark_message_id = message_id

    def is_after_watermark(self, created_at: datetime, message_id: str) -> bool:
        """True iff (created_at, message_id) sorts strictly after the watermark.

        Both datetimes must be timezone-aware (the deadsimple API returns UTC
        ISO 8601). Mixing naive and aware datetimes raises TypeError at the
        comparison.
        """
        if self.watermark_created_at is None:
            return True
        return (created_at, message_id) > (self.watermark_created_at, self.watermark_message_id)

    def save(self) -> None:
        """Atomic write via temp-file + os.replace."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "watermark_created_at": (
                self.watermark_created_at.isoformat() if self.watermark_created_at else None
            ),
            "watermark_message_id": self.watermark_message_id,
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
