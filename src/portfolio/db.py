"""SQLite schema, connection helpers, and init for the balance-sheet store.

One wide `assets` table (nullable per-class columns + a JSON `extra` overflow)
and a `liabilities` table. Views give the canned rollups so the model can read
them without re-summing. Writes go only through engine.py."""
from __future__ import annotations

import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
  id          TEXT PRIMARY KEY,
  class       TEXT NOT NULL,
  label       TEXT NOT NULL,
  ticker      TEXT, qty REAL, avg_cost REAL,
  cost_basis  REAL,
  value       REAL NOT NULL,
  value_asof  TEXT,
  acquired    TEXT,
  account     TEXT,
  extra       TEXT,
  UNIQUE(class, label)
);

CREATE TABLE IF NOT EXISTS liabilities (
  id           TEXT PRIMARY KEY,
  class        TEXT NOT NULL,
  label        TEXT NOT NULL,
  balance      REAL NOT NULL, apr REAL,
  linked_asset TEXT REFERENCES assets(id)
);

CREATE VIEW IF NOT EXISTS v_networth AS
  SELECT a.assets, l.liabilities, a.assets - l.liabilities AS net_worth
  FROM (SELECT COALESCE(SUM(value),0)   AS assets      FROM assets) a,
       (SELECT COALESCE(SUM(balance),0) AS liabilities FROM liabilities) l;

CREATE VIEW IF NOT EXISTS v_rollup_by_class AS
  SELECT class, COALESCE(SUM(value),0) AS value, COUNT(*) AS n
  FROM assets GROUP BY class;

CREATE VIEW IF NOT EXISTS v_collectibles_pnl AS
  SELECT label, cost_basis, value, value - cost_basis AS unrealized, acquired
  FROM assets WHERE class = 'collectible';

-- Trade ledger: append-only event log for qty-bearing positions. A buy mints a
-- new asset lot; a sell draws down a named lot and records realized P/L. The
-- asset_id is a soft reference (no FK) so a sell that closes a lot can delete
-- the asset row while the trade survives as history.
CREATE TABLE IF NOT EXISTS trades (
  id              TEXT PRIMARY KEY,
  trade_date      TEXT NOT NULL,
  side            TEXT NOT NULL,
  ticker          TEXT NOT NULL,
  qty             REAL NOT NULL,
  price           REAL NOT NULL,
  fees            REAL,
  asset_id        TEXT,
  account         TEXT,
  cost_basis_sold REAL,
  proceeds        REAL,
  realized_pnl    REAL,
  long_term       INTEGER,
  note            TEXT,
  created_at      TEXT
);
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
