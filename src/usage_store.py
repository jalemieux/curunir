# src/usage_store.py
"""Persistent per-call LLM usage store backed by SQLite.

One row per ``call_llm`` invocation, mapped to OpenRouter billing dimensions
(prompt/completion/cached prompt/reasoning/image/audio tokens, and per-call
USD cost when the provider returns one). Reads are served via ``summary()``
for the CLI; the agent only writes.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)


@dataclass
class UsageRecord:
    ts: datetime
    session_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_prompt_tokens: int = 0
    reasoning_tokens: int = 0
    image_tokens: int = 0
    audio_tokens: int = 0
    cost_usd: float | None = None
    elapsed_sec: float = 0.0


_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  session_id TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_tokens INTEGER NOT NULL,
  completion_tokens INTEGER NOT NULL,
  cached_prompt_tokens INTEGER NOT NULL DEFAULT 0,
  reasoning_tokens INTEGER NOT NULL DEFAULT 0,
  image_tokens INTEGER NOT NULL DEFAULT 0,
  audio_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL,
  elapsed_sec REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts);
"""


class UsageStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def record(self, row: UsageRecord) -> None:
        ts = row.ts
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO usage (
                    ts, session_id, model,
                    prompt_tokens, completion_tokens,
                    cached_prompt_tokens, reasoning_tokens,
                    image_tokens, audio_tokens,
                    cost_usd, elapsed_sec
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts.astimezone(timezone.utc).isoformat(),
                    row.session_id,
                    row.model,
                    int(row.prompt_tokens),
                    int(row.completion_tokens),
                    int(row.cached_prompt_tokens),
                    int(row.reasoning_tokens),
                    int(row.image_tokens),
                    int(row.audio_tokens),
                    row.cost_usd,
                    float(row.elapsed_sec),
                ),
            )

    def summary(
        self,
        window: timedelta,
        group_by: Literal["model", "day", "session", "day_session"] = "model",
    ) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - window).isoformat()
        if group_by == "model":
            group_expr = "model"
            select_expr = "model"
        elif group_by == "day":
            group_expr = "substr(ts, 1, 10)"
            select_expr = f"{group_expr} AS day"
        elif group_by == "session":
            # Group by raw session_id; scheduled-job runs (each a unique
            # ``sched:<id>:<ts>``) are collapsed under ``sched:<id>`` below.
            group_expr = "session_id"
            select_expr = "session_id"
        elif group_by == "day_session":
            # One row per (calendar-day, raw session_id). Deliberately NOT
            # collapsed — each scheduled firing ``sched:<id>:<ts>`` stays its
            # own row so the By-day drill-down can show individual runs.
            group_expr = "substr(ts, 1, 10), session_id"
            select_expr = "substr(ts, 1, 10) AS day, session_id"
        else:
            raise ValueError(f"unknown group_by: {group_by}")

        sql = f"""
            SELECT
                {select_expr},
                COUNT(*) AS calls,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(SUM(cached_prompt_tokens), 0) AS cached_prompt_tokens,
                COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                COALESCE(SUM(image_tokens), 0) AS image_tokens,
                COALESCE(SUM(audio_tokens), 0) AS audio_tokens,
                COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                COALESCE(SUM(elapsed_sec), 0.0) AS elapsed_sec
            FROM usage
            WHERE ts >= ?
            GROUP BY {group_expr}
            ORDER BY cost_usd DESC
        """
        with self._lock:
            cur = self._conn.execute(sql, (cutoff,))
            rows = [dict(r) for r in cur.fetchall()]
        if group_by == "session":
            rows = _collapse_sessions(rows)
        return rows

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# Numeric columns summed when collapsing scheduled-job runs into one session.
_SESSION_SUM_COLS = (
    "calls", "prompt_tokens", "completion_tokens", "cached_prompt_tokens",
    "reasoning_tokens", "image_tokens", "audio_tokens", "cost_usd", "elapsed_sec",
)


def normalize_session(session_id: str) -> str:
    """Collapse a scheduled run's ``sched:<id>:<ts>`` to its job key ``sched:<id>``.

    Every scheduler dispatch mints a unique session ``sched:<id>:<timestamp>``;
    rolling those up by job id keeps a recurring task to a single breakdown row.
    Non-scheduled session ids (chat UUIDs, ``cli``, email thread ids) pass
    through unchanged.
    """
    if session_id.startswith("sched:"):
        parts = session_id.split(":")
        if len(parts) >= 3:
            return ":".join(parts[:-1])
    return session_id


def _collapse_sessions(rows: list[dict]) -> list[dict]:
    """Merge per-``session_id`` rows by :func:`normalize_session`.

    Returns rows keyed ``session`` (the normalized id), summing token/cost/call
    columns, sorted by total tokens (prompt + completion) descending.
    """
    merged: dict[str, dict] = {}
    for r in rows:
        key = normalize_session(r["session_id"])
        m = merged.get(key)
        if m is None:
            m = {"session": key, **{c: 0 for c in _SESSION_SUM_COLS}}
            merged[key] = m
        for c in _SESSION_SUM_COLS:
            m[c] += r.get(c) or 0
    out = list(merged.values())
    out.sort(
        key=lambda r: r["prompt_tokens"] + r["completion_tokens"], reverse=True
    )
    return out
