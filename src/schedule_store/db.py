"""SQLite schema, connection helpers, and init for the schedule store.

One `schedules` table whose columns mirror the legacy `schedules.json` fields —
editable fields (cron/skill/prompt/enabled) plus run metadata
(last_run/last_attempt_at/last_status/last_error). WAL mode, consistent with
`usage.db`. Writes go only through engine.py."""
from __future__ import annotations

import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
  id              TEXT PRIMARY KEY,
  cron            TEXT NOT NULL,
  skill           TEXT,
  prompt          TEXT,
  enabled         INTEGER NOT NULL DEFAULT 1,
  last_run        INTEGER NOT NULL DEFAULT 0,
  last_attempt_at INTEGER NOT NULL DEFAULT 0,
  last_status     TEXT,
  last_error      TEXT
);
"""


def connect(path: str, *, readonly: bool = False) -> sqlite3.Connection:
    """Open a connection. `readonly=True` uses a URI mode=ro handle so a stray
    write raises sqlite3.OperationalError instead of mutating the store."""
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(path)
        con.execute("PRAGMA journal_mode = WAL")
    con.row_factory = sqlite3.Row
    return con


def init_db(path: str) -> None:
    """Create the schema if absent. Idempotent."""
    con = connect(path)
    try:
        con.executescript(_SCHEMA)
        con.commit()
    finally:
        con.close()
