"""Anchor helper for the position-tracking eval family (T*).

Reports the net-worth truth the T graders anchor to by querying the SAME SQLite
portfolio store and the SAME views the agent's engine uses — `v_networth`,
`v_rollup_by_class`, `v_collectibles_pnl` (the A/B/D coordination-note contract).
Agent and grader read identical views over identical data, so neither can drift.

There is deliberately NO fallback math and NO in-memory rebuild here: the anchor
consumes the engine's store or it fails loudly. The store is seeded for a run
from `fixtures/portfolio.sql` (the frozen, synthetic source of truth). Point the
anchor at a different DB with `CURUNIR_PORTFOLIO_DB`.

Subcommands (each prints one JSON object):

    python eval/finance/_networth.py total
        -> {"net_worth", "assets", "liabilities"}             # SELECT * FROM v_networth

    python eval/finance/_networth.py rollup
        -> {"equities", "real_estate_equity", "collectibles",
            "cash", "debt", "total"}                          # SELECT * FROM v_rollup_by_class

    python eval/finance/_networth.py re-equity paladin
        -> {"equity"}                       # property value − its mortgage balance

    python eval/finance/_networth.py collectibles
        -> {"value", "cost_basis", "unrealized_gain", "missing_basis"}
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DB = Path(os.environ.get("CURUNIR_PORTFOLIO_DB", REPO / "context" / "memory" / "portfolio.db"))


def _connect() -> sqlite3.Connection:
    if not DB.exists():
        raise SystemExit(
            f"portfolio store not found at {DB}. The T-family anchors read the seeded "
            "SQLite store (built from fixtures/portfolio.sql by the A/B/D engine) — run "
            "with the portfolio fixture in place, or set CURUNIR_PORTFOLIO_DB."
        )
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _row(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict:
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    return {k: row[k] for k in row.keys()} if row is not None else {}


def _round(d: dict) -> dict:
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}


def cmd_total(conn: sqlite3.Connection) -> dict:
    return _round(_row(conn, "SELECT net_worth, assets, liabilities FROM v_networth"))


def cmd_rollup(conn: sqlite3.Connection) -> dict:
    return _round(_row(conn, "SELECT equities, real_estate_equity, collectibles, cash, "
                             "debt, total FROM v_rollup_by_class"))


def cmd_re_equity(conn: sqlite3.Connection, name: str) -> dict:
    """Equity in the property whose label contains `name` (case-insensitive)."""
    r = _row(
        conn,
        "SELECT a.value - COALESCE("
        "  (SELECT SUM(l.balance) FROM liabilities l"
        "   WHERE l.type='mortgage' AND l.property = a.label), 0) AS equity "
        "FROM assets a WHERE a.class='real_estate' AND lower(a.label) LIKE '%' || ? || '%' "
        "LIMIT 1",
        (name.lower(),),
    )
    if not r:
        raise SystemExit(f"no real-estate asset matching {name!r}")
    return _round(r)


def cmd_collectibles(conn: sqlite3.Connection) -> dict:
    r = _row(conn, "SELECT value, cost_basis, unrealized_gain, missing_basis_count "
                   "FROM v_collectibles_pnl")
    missing = [row["label"] for row in conn.execute(
        "SELECT label FROM assets WHERE class='collectible' AND cost_basis IS NULL")]
    return {
        "value": round(r["value"], 2),
        "cost_basis": round(r["cost_basis"], 2),
        "unrealized_gain": round(r["unrealized_gain"], 2),
        "missing_basis": missing,
    }


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    conn = _connect()
    try:
        sub = sys.argv[1]
        if sub == "total":
            out = cmd_total(conn)
        elif sub == "rollup":
            out = cmd_rollup(conn)
        elif sub == "re-equity":
            out = cmd_re_equity(conn, sys.argv[2])
        elif sub == "collectibles":
            out = cmd_collectibles(conn)
        else:
            raise SystemExit(f"unknown subcommand {sub!r}")
    finally:
        conn.close()
    print(json.dumps(out))


if __name__ == "__main__":
    main()
