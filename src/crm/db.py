"""SQLite schema, connection helpers, and init for the mini-CRM store.

One wide `leads` table (nullable per-field columns + a JSON `extra` overflow)
and an append-only `interactions` ledger. Views give the canned rollups so the
model/UI can read them without re-aggregating. Writes go only through engine.py.

Mirrors src/portfolio/db.py — the proven four-tier shape — so the CRM is
harness-agnostic (pure SQLite + stdlib)."""
from __future__ import annotations

import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  email       TEXT,
  company     TEXT,
  source      TEXT,
  stage       TEXT NOT NULL,
  owner       TEXT,
  note        TEXT,
  created_at  TEXT,
  updated_at  TEXT,
  extra       TEXT,
  UNIQUE(email)
);

-- Interaction ledger: append-only event log for a lead's history (emails,
-- calls, notes, stage changes). The lead_id is a soft reference (no FK) so a
-- lead's deletion leaves its interactions as durable history.
CREATE TABLE IF NOT EXISTS interactions (
  id          TEXT PRIMARY KEY,
  lead_id     TEXT,
  kind        TEXT NOT NULL,
  body        TEXT,
  occurred_at TEXT,
  created_at  TEXT
);

CREATE INDEX IF NOT EXISTS ix_interactions_lead ON interactions(lead_id);

CREATE VIEW IF NOT EXISTS v_pipeline_by_stage AS
  SELECT stage, COUNT(*) AS n FROM leads GROUP BY stage;

CREATE VIEW IF NOT EXISTS v_lead_latest_activity AS
  SELECT l.*,
    (SELECT i.kind FROM interactions i WHERE i.lead_id = l.id
       ORDER BY i.occurred_at DESC, i.created_at DESC, i.id DESC LIMIT 1)
       AS last_kind,
    (SELECT i.occurred_at FROM interactions i WHERE i.lead_id = l.id
       ORDER BY i.occurred_at DESC, i.created_at DESC, i.id DESC LIMIT 1)
       AS last_activity_at
  FROM leads l;
"""


def connect(path: str, *, readonly: bool = False) -> sqlite3.Connection:
    """Open a connection. `readonly=True` uses a URI mode=ro handle so a stray
    write raises sqlite3.OperationalError instead of mutating the store."""
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(path)
        con.execute("PRAGMA foreign_keys = ON")
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
