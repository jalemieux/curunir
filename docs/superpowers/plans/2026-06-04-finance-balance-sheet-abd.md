# Finance Balance-Sheet Capability (A/B/D) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the finance persona a deterministic personal-balance-sheet capability so a local model never hand-sums a portfolio or hand-edits the store — assets/liabilities live in SQLite, all math/writes go through a Python engine, exposed via two interchangeable surfaces (CLI + opt-in tool) that the eval suite picks between.

**Architecture:** A single importable engine under `src/portfolio/` owns a SQLite DB (`context/memory/portfolio.db`) — tables `assets`/`liabilities`, views for rollups, validated writes, deterministic `refresh()`. Two thin adapters expose it: a `bash`-invoked CLI (`skills/balance-sheet/portfolio.py`, mirroring `skills/yfinance/yfin.py`) and an opt-in tool (`src/tools/portfolio_tool.py`, unlocked by the `balance-sheet` skill). Persona prompts route to it and forbid hand-math.

**Tech Stack:** Python 3.12, stdlib `sqlite3` + `json` + `argparse`, pytest. No new dependencies. Engine functions are synchronous and importable for tests (the `yfin.py` convention).

**Spec:** `docs/superpowers/specs/2026-06-04-finance-balance-sheet-abd-design.md`

**Out of scope (fast-follow / other thread):** the scheduled refresh coroutine in `run.py`; the eval suite C (separate spec/thread — this plan only ensures the engine is importable so C's anchor can call it).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/portfolio/__init__.py` | Package marker; re-export `engine` public functions. |
| `src/portfolio/db.py` | Schema DDL (tables + views), `connect(path, readonly)`, `init_db(path)`. |
| `src/portfolio/engine.py` | All operations: reads (`networth`, `rollup`, `list_assets`, `show`, `re_equity`, `pnl`, `query`), writes (`add_asset`, `update_asset`, `remove_asset`, `import_rows`), `refresh`, `render_markdown`. Pure-ish functions taking a `db` path. |
| `skills/balance-sheet/portfolio.py` | CLI adapter: `cmd_*` → engine → JSON on stdout (yfin.py pattern). |
| `skills/balance-sheet/SKILL.md` | Skill doc: data model, command surface, disciplines; declares `tools: portfolio`. |
| `src/tools/portfolio_tool.py` | Opt-in tool executor `exec_portfolio(args, config) -> str`. |
| `src/tools/schemas.py` | (modify) register the `portfolio` opt-in tool schema. |
| `src/tools/dispatcher.py` | (modify) route `"portfolio"` to `exec_portfolio`. |
| `personas/finance/persona.yaml` | (modify) add `balance-sheet` to the allowlist. |
| `personas/finance/prompts/10-domain.md` | (modify) compute-don't-recite rule. |
| `personas/finance/prompts/20-guardrails.md` | (modify) verify-before-cite + capture basis/date. |
| `context/memory/README.md` | (modify) route asset facts to the engine, not prose. |
| `scripts/migrate_portfolio.py` | One-off: parse `context/memory/portfolios.md` → `portfolio.db`. |
| `tests/test_portfolio_engine.py` | Engine unit tests. |
| `tests/test_portfolio_cli.py` | CLI adapter smoke tests. |
| `tests/test_portfolio_tool.py` | Opt-in tool executor test. |

**Asset classes (closed set):** `equity`, `real_estate`, `collectible`, `physical`, `cash`, `private`, `retirement`. **Liability classes:** `mortgage`, `loc`, `loan`. **Market-priced (refreshable):** `equity`, `physical`, `crypto` (crypto via an `equity`-style ticker like `BTC-USD`).

---

## Task 1: SQLite schema + db module

**Files:**
- Create: `src/portfolio/__init__.py`
- Create: `src/portfolio/db.py`
- Test: `tests/test_portfolio_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_portfolio_engine.py
import sqlite3
from src.portfolio import db as pdb


def test_init_db_creates_tables_and_views(tmp_path):
    path = str(tmp_path / "portfolio.db")
    pdb.init_db(path)
    con = sqlite3.connect(path)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    con.close()
    assert {"assets", "liabilities",
            "v_networth", "v_rollup_by_class", "v_collectibles_pnl"} <= names


def test_readonly_connection_rejects_writes(tmp_path):
    path = str(tmp_path / "portfolio.db")
    pdb.init_db(path)
    con = pdb.connect(path, readonly=True)
    import pytest
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO assets(id,class,label,value) VALUES('x','cash','x',1)")
    con.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_portfolio_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.portfolio'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/portfolio/__init__.py
"""Personal balance-sheet store: SQLite-backed assets/liabilities + a
deterministic engine. The agent reaches this via the CLI or opt-in tool;
the eval anchor imports it directly."""
```

```python
# src/portfolio/db.py
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
  extra       TEXT,                       -- JSON blob, class-specific
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_portfolio_engine.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/portfolio/__init__.py src/portfolio/db.py tests/test_portfolio_engine.py
git commit -m "feat(portfolio): SQLite schema + db module for balance sheet"
```

---

## Task 2: Engine writes — add_asset (validation + dedup warning)

**Files:**
- Create: `src/portfolio/engine.py`
- Test: `tests/test_portfolio_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_portfolio_engine.py
from src.portfolio import engine


def _fresh(tmp_path):
    path = str(tmp_path / "portfolio.db")
    pdb.init_db(path)
    return path


def test_add_asset_assigns_id_and_persists(tmp_path):
    path = _fresh(tmp_path)
    res = engine.add_asset(path, {"class": "cash", "label": "Checking", "value": 1000})
    assert res["id"]
    rows = engine.list_assets(path)
    assert len(rows) == 1 and rows[0]["label"] == "Checking"


def test_add_asset_requires_class_label_value(tmp_path):
    path = _fresh(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        engine.add_asset(path, {"class": "cash", "label": "x"})  # no value


def test_add_asset_warns_on_missing_basis_and_date(tmp_path):
    path = _fresh(tmp_path)
    res = engine.add_asset(path, {"class": "collectible", "label": "Watch A", "value": 5000})
    assert any("cost_basis" in w for w in res["warnings"])
    assert any("acquired" in w for w in res["warnings"])


def test_add_asset_warns_on_near_duplicate_label(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "collectible", "label": "Rolex GMT Batman",
                            "value": 15839, "cost_basis": 12000, "acquired": "2022-01-01"})
    res = engine.add_asset(path, {"class": "collectible", "label": "Rolex GMT Batman v2",
                                  "value": 16744, "cost_basis": 12500, "acquired": "2022-06-01"})
    assert any("similar" in w.lower() for w in res["warnings"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_portfolio_engine.py -k add_asset -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.portfolio.engine'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/portfolio/engine.py
"""Balance-sheet engine: all reads, writes, refresh, and rendering over the
SQLite store. Synchronous; importable for tests and for the eval anchor.

Every public function takes the DB `path` as its first argument. Reads never
mutate; the only writers are add_asset / update_asset / remove_asset /
import_rows / refresh."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date

from src.portfolio import db as pdb

ASSET_CLASSES = {"equity", "real_estate", "collectible", "physical",
                 "cash", "private", "retirement"}
MARKET_PRICED = {"equity", "physical", "crypto"}
_REQUIRED = ("class", "label", "value")
_COLUMNS = ("id", "class", "label", "ticker", "qty", "avg_cost",
            "cost_basis", "value", "value_asof", "acquired", "account", "extra")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "asset"


def _norm(label: str) -> str:
    """Normalize a label for fuzzy duplicate detection."""
    return re.sub(r"[^a-z0-9]+", "", label.lower())


def add_asset(path: str, fields: dict) -> dict:
    """Insert one asset. Returns {id, warnings[]}. Raises ValueError on a
    missing required field or an unknown class. Warns (does not block) on a
    missing cost_basis/acquired or a near-duplicate label."""
    missing = [k for k in _REQUIRED if fields.get(k) in (None, "")]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")
    if fields["class"] not in ASSET_CLASSES:
        raise ValueError(f"unknown class {fields['class']!r}; "
                         f"valid: {sorted(ASSET_CLASSES)}")

    warnings: list[str] = []
    if fields.get("cost_basis") in (None, ""):
        warnings.append("no cost_basis recorded — gain/tax cannot be computed")
    if fields.get("acquired") in (None, ""):
        warnings.append("no acquired date recorded — holding period unknown")

    con = pdb.connect(path)
    try:
        target = _norm(fields["label"])
        for row in con.execute("SELECT label FROM assets WHERE class = ?",
                               (fields["class"],)):
            other = _norm(row["label"])
            if other and (other in target or target in other):
                warnings.append(
                    f"a similar {fields['class']} already exists: "
                    f"{row['label']!r} — confirm this isn't a duplicate")
                break

        base = _slug(fields["label"])
        new_id = base
        i = 2
        existing = {r["id"] for r in con.execute("SELECT id FROM assets")}
        while new_id in existing:
            new_id, i = f"{base}-{i}", i + 1

        extra = fields.get("extra")
        record = {
            "id": new_id, "class": fields["class"], "label": fields["label"],
            "ticker": fields.get("ticker"), "qty": fields.get("qty"),
            "avg_cost": fields.get("avg_cost"), "cost_basis": fields.get("cost_basis"),
            "value": float(fields["value"]),
            "value_asof": fields.get("value_asof") or date.today().isoformat(),
            "acquired": fields.get("acquired"), "account": fields.get("account"),
            "extra": json.dumps(extra) if isinstance(extra, (dict, list)) else extra,
        }
        con.execute(
            f"INSERT INTO assets ({','.join(_COLUMNS)}) "
            f"VALUES ({','.join('?' for _ in _COLUMNS)})",
            tuple(record[c] for c in _COLUMNS),
        )
        con.commit()
    finally:
        con.close()
    return {"id": new_id, "warnings": warnings}


def list_assets(path: str, cls: str | None = None, account: str | None = None) -> list[dict]:
    """All assets, optionally filtered by class and/or account."""
    sql = "SELECT * FROM assets"
    clauses, params = [], []
    if cls:
        clauses.append("class = ?"); params.append(cls)
    if account:
        clauses.append("account = ?"); params.append(account)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    con = pdb.connect(path)
    try:
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_portfolio_engine.py -k "add_asset or list" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/portfolio/engine.py tests/test_portfolio_engine.py
git commit -m "feat(portfolio): add_asset with validation, dedup + missing-field warnings"
```

---

## Task 3: Engine writes — update_asset, remove_asset

**Files:**
- Modify: `src/portfolio/engine.py`
- Test: `tests/test_portfolio_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_portfolio_engine.py
def test_update_asset_sets_fields(tmp_path):
    path = _fresh(tmp_path)
    aid = engine.add_asset(path, {"class": "cash", "label": "Checking", "value": 1000})["id"]
    engine.update_asset(path, aid, {"value": 1500})
    assert engine.show(path, aid)["value"] == 1500


def test_remove_asset_deletes(tmp_path):
    path = _fresh(tmp_path)
    aid = engine.add_asset(path, {"class": "cash", "label": "Checking", "value": 1000})["id"]
    engine.remove_asset(path, aid)
    assert engine.list_assets(path) == []


def test_update_unknown_id_raises(tmp_path):
    path = _fresh(tmp_path)
    import pytest
    with pytest.raises(KeyError):
        engine.update_asset(path, "nope", {"value": 1})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_portfolio_engine.py -k "update_asset or remove_asset" -v`
Expected: FAIL — `AttributeError: module 'src.portfolio.engine' has no attribute 'show'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/portfolio/engine.py
_UPDATABLE = {"label", "ticker", "qty", "avg_cost", "cost_basis", "value",
              "value_asof", "acquired", "account", "extra"}


def show(path: str, asset_id: str) -> dict:
    con = pdb.connect(path)
    try:
        row = con.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        raise KeyError(f"no asset with id {asset_id!r}")
    return dict(row)


def update_asset(path: str, asset_id: str, fields: dict) -> dict:
    """Update whitelisted columns on one asset. Raises KeyError if absent."""
    sets = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not sets:
        raise ValueError(f"no updatable fields in {list(fields)}")
    if "extra" in sets and isinstance(sets["extra"], (dict, list)):
        sets["extra"] = json.dumps(sets["extra"])
    con = pdb.connect(path)
    try:
        if con.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone() is None:
            raise KeyError(f"no asset with id {asset_id!r}")
        assigns = ", ".join(f"{k} = ?" for k in sets)
        con.execute(f"UPDATE assets SET {assigns} WHERE id = ?",
                    (*sets.values(), asset_id))
        con.commit()
    finally:
        con.close()
    return show(path, asset_id)


def remove_asset(path: str, asset_id: str) -> dict:
    con = pdb.connect(path)
    try:
        cur = con.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        con.commit()
    finally:
        con.close()
    if cur.rowcount == 0:
        raise KeyError(f"no asset with id {asset_id!r}")
    return {"removed": asset_id}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_portfolio_engine.py -k "update_asset or remove_asset" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/portfolio/engine.py tests/test_portfolio_engine.py
git commit -m "feat(portfolio): update_asset/remove_asset/show"
```

---

## Task 4: Liabilities + networth + rollup

**Files:**
- Modify: `src/portfolio/engine.py`
- Test: `tests/test_portfolio_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_portfolio_engine.py
def test_networth_is_assets_minus_liabilities(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 100000})
    engine.add_asset(path, {"class": "real_estate", "label": "House", "value": 1000000})
    engine.add_liability(path, {"class": "mortgage", "label": "House mtg",
                                "balance": 400000, "linked_asset": "house"})
    nw = engine.networth(path)
    assert nw["assets"] == 1100000
    assert nw["liabilities"] == 400000
    assert nw["net_worth"] == 700000


def test_rollup_buckets_net_real_estate_to_equity(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"id": "house", "class": "real_estate", "label": "House",
                            "value": 1000000})
    engine.add_asset(path, {"class": "equity", "label": "VOO", "value": 200000})
    engine.add_asset(path, {"class": "collectible", "label": "Watch", "value": 50000})
    engine.add_liability(path, {"class": "mortgage", "label": "mtg",
                                "balance": 400000, "linked_asset": "house"})
    r = engine.rollup(path)
    assert r["real_estate_equity"] == 600000   # 1,000,000 - 400,000
    assert r["equities"] == 200000
    assert r["collectibles"] == 50000
    assert r["debt"] == 400000
    assert r["net_worth"] == 850000            # 1,250,000 - 400,000
```

Note: `add_asset` must accept an explicit `id` (used by `linked_asset`). Extend it.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_portfolio_engine.py -k "networth or rollup" -v`
Expected: FAIL — `AttributeError: ... has no attribute 'add_liability'`

- [ ] **Step 3: Write minimal implementation**

First, allow a caller-supplied `id` in `add_asset` — change the id block:

```python
# in src/portfolio/engine.py add_asset(), replace the id-generation block with:
        base = _slug(fields.get("id") or fields["label"])
        new_id = base
        i = 2
        existing = {r["id"] for r in con.execute("SELECT id FROM assets")}
        while new_id in existing:
            new_id, i = f"{base}-{i}", i + 1
```

Then add liabilities + rollups:

```python
# add to src/portfolio/engine.py
_LIAB_COLUMNS = ("id", "class", "label", "balance", "apr", "linked_asset")
RETIREMENT_AS_EQUITY = ("equity", "retirement")


def add_liability(path: str, fields: dict) -> dict:
    for k in ("class", "label", "balance"):
        if fields.get(k) in (None, ""):
            raise ValueError(f"missing required liability field: {k}")
    con = pdb.connect(path)
    try:
        base = _slug(fields.get("id") or fields["label"])
        new_id, i = base, 2
        existing = {r["id"] for r in con.execute("SELECT id FROM liabilities")}
        while new_id in existing:
            new_id, i = f"{base}-{i}", i + 1
        rec = {"id": new_id, "class": fields["class"], "label": fields["label"],
               "balance": float(fields["balance"]), "apr": fields.get("apr"),
               "linked_asset": fields.get("linked_asset")}
        con.execute(
            f"INSERT INTO liabilities ({','.join(_LIAB_COLUMNS)}) "
            f"VALUES ({','.join('?' for _ in _LIAB_COLUMNS)})",
            tuple(rec[c] for c in _LIAB_COLUMNS))
        con.commit()
    finally:
        con.close()
    return {"id": new_id}


def networth(path: str) -> dict:
    con = pdb.connect(path)
    try:
        return dict(con.execute("SELECT * FROM v_networth").fetchone())
    finally:
        con.close()


def rollup(path: str) -> dict:
    """Finance-meaningful buckets: equities (incl. retirement), real-estate
    EQUITY (property value minus linked mortgages), collectibles, physical,
    cash, private, debt, and net worth. The model reads this; it never sums."""
    con = pdb.connect(path)
    try:
        by_class = {r["class"]: r["value"]
                    for r in con.execute("SELECT * FROM v_rollup_by_class")}
        re_value = by_class.get("real_estate", 0.0)
        re_debt = con.execute(
            "SELECT COALESCE(SUM(l.balance),0) AS d FROM liabilities l "
            "JOIN assets a ON a.id = l.linked_asset WHERE a.class = 'real_estate'"
        ).fetchone()["d"]
        debt = con.execute("SELECT COALESCE(SUM(balance),0) AS d FROM liabilities").fetchone()["d"]
        assets_total = con.execute("SELECT COALESCE(SUM(value),0) AS a FROM assets").fetchone()["a"]
    finally:
        con.close()
    return {
        "equities": round(sum(by_class.get(c, 0.0) for c in RETIREMENT_AS_EQUITY), 2),
        "real_estate_equity": round(re_value - re_debt, 2),
        "collectibles": round(by_class.get("collectible", 0.0), 2),
        "physical": round(by_class.get("physical", 0.0), 2),
        "cash": round(by_class.get("cash", 0.0), 2),
        "private": round(by_class.get("private", 0.0), 2),
        "debt": round(debt, 2),
        "net_worth": round(assets_total - debt, 2),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_portfolio_engine.py -k "networth or rollup or add_asset" -v`
Expected: PASS (add_asset tests still green after the id change)

- [ ] **Step 5: Commit**

```bash
git add src/portfolio/engine.py tests/test_portfolio_engine.py
git commit -m "feat(portfolio): liabilities, networth, finance-bucket rollup"
```

---

## Task 5: re_equity + pnl + read-only query

**Files:**
- Modify: `src/portfolio/engine.py`
- Test: `tests/test_portfolio_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_portfolio_engine.py
def test_re_equity(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"id": "paladin", "class": "real_estate",
                            "label": "Rental Property", "value": 1558400})
    engine.add_liability(path, {"class": "mortgage", "label": "Rental Property mtg",
                                "balance": 395309, "linked_asset": "paladin"})
    assert engine.re_equity(path, "paladin")["equity"] == 1163091


def test_pnl_collectibles_holding_period(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "collectible", "label": "Submariner",
                            "value": 12569, "cost_basis": 9200, "acquired": "2023-04-10"})
    p = engine.pnl(path, "collectible", today="2026-06-04")
    assert p["cost_basis"] == 9200 and p["value"] == 12569
    assert p["unrealized"] == 3369
    assert p["items"][0]["long_term"] is True   # held > 1 year


def test_query_is_readonly(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 5})
    rows = engine.query(path, "SELECT label, value FROM assets")
    assert rows == [{"label": "Cash", "value": 5}]
    import pytest
    with pytest.raises(Exception):
        engine.query(path, "DELETE FROM assets")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_portfolio_engine.py -k "re_equity or pnl or query" -v`
Expected: FAIL — `AttributeError: ... has no attribute 're_equity'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/portfolio/engine.py
def re_equity(path: str, property_id: str) -> dict:
    con = pdb.connect(path)
    try:
        a = con.execute("SELECT value FROM assets WHERE id = ? AND class = 'real_estate'",
                        (property_id,)).fetchone()
        if a is None:
            raise KeyError(f"no real_estate asset with id {property_id!r}")
        d = con.execute("SELECT COALESCE(SUM(balance),0) AS d FROM liabilities "
                        "WHERE linked_asset = ?", (property_id,)).fetchone()["d"]
    finally:
        con.close()
    return {"value": a["value"], "debt": d, "equity": round(a["value"] - d, 2)}


def _years_between(start: str, end: str) -> float:
    from datetime import date as _d
    try:
        s = _d.fromisoformat(start); e = _d.fromisoformat(end)
    except (TypeError, ValueError):
        return 0.0
    return (e - s).days / 365.25


def pnl(path: str, cls: str = "collectible", today: str | None = None) -> dict:
    """Cost basis, unrealized gain, and per-item holding period for a class."""
    today = today or date.today().isoformat()
    items, basis, value = [], 0.0, 0.0
    for a in list_assets(path, cls=cls):
        cb = a.get("cost_basis")
        held = _years_between(a.get("acquired"), today) if a.get("acquired") else None
        items.append({
            "label": a["label"], "value": a["value"], "cost_basis": cb,
            "unrealized": round(a["value"] - cb, 2) if cb is not None else None,
            "acquired": a.get("acquired"),
            "long_term": (held >= 1.0) if held is not None else None,
        })
        value += a["value"] or 0
        basis += cb or 0
    return {"class": cls, "cost_basis": round(basis, 2), "value": round(value, 2),
            "unrealized": round(value - basis, 2), "items": items}


def query(path: str, sql: str) -> list[dict]:
    """Run an arbitrary read-only SELECT. Opened mode=ro, so any write raises."""
    con = pdb.connect(path, readonly=True)
    try:
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_portfolio_engine.py -k "re_equity or pnl or query" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/portfolio/engine.py tests/test_portfolio_engine.py
git commit -m "feat(portfolio): re_equity, collectibles pnl + holding period, read-only query"
```

---

## Task 6: import_rows with account-total self-check

**Files:**
- Modify: `src/portfolio/engine.py`
- Test: `tests/test_portfolio_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_portfolio_engine.py
def test_import_rows_inserts_and_self_checks_ok(tmp_path):
    path = _fresh(tmp_path)
    rows = [
        {"class": "equity", "label": "VOO", "ticker": "VOO", "qty": 10,
         "cost_basis": 3000, "value": 7000},
        {"class": "equity", "label": "GLD", "ticker": "GLD", "qty": 5,
         "cost_basis": 1600, "value": 2200},
    ]
    res = engine.import_rows(path, rows, account="brokerage-7942",
                             stated_total=9200)
    assert res["imported"] == 2
    assert res["self_check"]["ok"] is True
    assert len(engine.list_assets(path, account="brokerage-7942")) == 2


def test_import_rows_flags_total_mismatch(tmp_path):
    path = _fresh(tmp_path)
    rows = [{"class": "equity", "label": "VOO", "value": 7000}]  # a row was dropped
    res = engine.import_rows(path, rows, account="brokerage-7942",
                             stated_total=9200)
    assert res["self_check"]["ok"] is False
    assert "9200" in res["self_check"]["detail"] or "9,200" in res["self_check"]["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_portfolio_engine.py -k import_rows -v`
Expected: FAIL — `AttributeError: ... has no attribute 'import_rows'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/portfolio/engine.py
def import_rows(path: str, rows: list[dict], account: str | None = None,
                stated_total: float | None = None, tolerance: float = 1.0) -> dict:
    """Bulk-insert mapped rows (e.g. from a brokerage CSV the LLM parsed).
    Stamps `account` on each. If `stated_total` is given (the export's account
    value), compares it to the summed imported value and reports a mismatch —
    the deterministic catch for a dropped/miscopied row."""
    imported, warnings = 0, []
    for row in rows:
        rec = dict(row)
        if account and not rec.get("account"):
            rec["account"] = account
        res = add_asset(path, rec)
        warnings.extend(f"[{rec.get('label','?')}] {w}" for w in res["warnings"])
        imported += 1

    self_check = {"ok": True, "detail": "no stated total to check against"}
    if stated_total is not None:
        got = sum(float(r.get("value") or 0) for r in rows)
        ok = abs(got - float(stated_total)) <= tolerance
        self_check = {
            "ok": ok,
            "imported_sum": round(got, 2),
            "stated_total": float(stated_total),
            "detail": ("matches stated account total" if ok else
                       f"imported sum {got:,.2f} != stated total {stated_total:,.2f} "
                       f"— a row may be missing or miscopied"),
        }
    return {"imported": imported, "account": account,
            "self_check": self_check, "warnings": warnings}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_portfolio_engine.py -k import_rows -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/portfolio/engine.py tests/test_portfolio_engine.py
git commit -m "feat(portfolio): import_rows with account-total self-check"
```

---

## Task 7: Deterministic refresh + render_markdown

**Files:**
- Modify: `src/portfolio/engine.py`
- Test: `tests/test_portfolio_engine.py`

**Note on `refresh`:** it re-prices market-priced assets by shelling out to the
existing yfinance CLI (`skills/yfinance/yfin.py quote <TICKER>` → JSON with a
price field), the same way the eval anchors shell out. The test injects a fake
quoter so it stays hermetic (no network).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_portfolio_engine.py
def test_refresh_reprices_only_market_priced(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "equity", "label": "VOO", "ticker": "VOO",
                            "qty": 10, "value": 7000})
    engine.add_asset(path, {"class": "collectible", "label": "Watch", "value": 5000})

    def fake_quote(ticker):           # qty 10 * 700 = 7000 -> 8000 at 800
        return {"VOO": 800.0}[ticker]

    res = engine.refresh(path, quoter=fake_quote)
    assert res["repriced"] == 1
    assert engine.show(path, "voo")["value"] == 8000
    # collectible untouched (no live feed)
    assert engine.show(path, "watch")["value"] == 5000


def test_render_markdown_has_networth_and_warning(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 1000})
    md = engine.render_markdown(path)
    assert "do not hand-edit" in md.lower()
    assert "Net Worth" in md and "1,000" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_portfolio_engine.py -k "refresh or render_markdown" -v`
Expected: FAIL — `AttributeError: ... has no attribute 'refresh'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/portfolio/engine.py
def _yfin_quote(ticker: str) -> float:
    """Default quoter: shell out to the yfinance CLI. Returns the last price."""
    import os
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    proc = subprocess.run([sys.executable, "skills/yfinance/yfin.py", "quote", ticker],
                          capture_output=True, text=True, timeout=30, cwd=root)
    data = json.loads(proc.stdout)
    price = data.get("price") or data.get("last") or data.get("regularMarketPrice")
    if price is None:
        raise ValueError(f"no price in yfin quote for {ticker}: {data}")
    return float(price)


def refresh(path: str, quoter=None) -> dict:
    """Deterministically re-price market-priced assets (equity/physical/crypto)
    that carry a ticker and qty: value = qty * live price. Illiquid classes are
    left untouched. `quoter(ticker)->price` is injectable for tests."""
    quoter = quoter or _yfin_quote
    today = date.today().isoformat()
    repriced, errors = 0, []
    for a in list_assets(path):
        if a["class"] not in MARKET_PRICED or not a.get("ticker") or a.get("qty") in (None, ""):
            continue
        try:
            price = quoter(a["ticker"])
        except Exception as exc:  # noqa: BLE001 — one bad ticker shouldn't abort all
            errors.append(f"{a['ticker']}: {exc}")
            continue
        update_asset(path, a["id"],
                     {"value": round(float(a["qty"]) * price, 2), "value_asof": today})
        repriced += 1
    return {"repriced": repriced, "errors": errors, "asof": today}


def render_markdown(path: str) -> str:
    """A read-only human view of the store, regenerated from the DB. This is
    the generated `portfolios.md` — never hand-edited."""
    nw = networth(path)
    roll = rollup(path)
    lines = [
        "# Portfolio (generated — do not hand-edit; source of truth is portfolio.db)",
        "", "## Net Worth", "",
        f"- Total assets: {nw['assets']:,.0f}",
        f"- Total liabilities: {nw['liabilities']:,.0f}",
        f"- **Net Worth: {nw['net_worth']:,.0f}**", "",
        "## Rollup by bucket", "",
        f"- Equities (incl. retirement): {roll['equities']:,.0f}",
        f"- Real-estate equity: {roll['real_estate_equity']:,.0f}",
        f"- Collectibles: {roll['collectibles']:,.0f}",
        f"- Physical: {roll['physical']:,.0f}",
        f"- Cash: {roll['cash']:,.0f}",
        f"- Private: {roll['private']:,.0f}",
        f"- Debt: {roll['debt']:,.0f}", "",
        "## Holdings", "",
        "| class | label | qty | cost basis | value | acquired | account |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for a in list_assets(path):
        lines.append(
            f"| {a['class']} | {a['label']} | {a.get('qty') or ''} | "
            f"{a.get('cost_basis') or ''} | {a['value']:,.0f} | "
            f"{a.get('acquired') or ''} | {a.get('account') or ''} |")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_portfolio_engine.py -k "refresh or render_markdown" -v`
Expected: PASS. Then full engine suite: `pytest tests/test_portfolio_engine.py -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio/engine.py tests/test_portfolio_engine.py
git commit -m "feat(portfolio): deterministic refresh + markdown render"
```

---

## Task 8: CLI adapter (skills/balance-sheet/portfolio.py)

**Files:**
- Create: `skills/balance-sheet/portfolio.py`
- Test: `tests/test_portfolio_cli.py`

The CLI mirrors `skills/yfinance/yfin.py`: subcommands print JSON to stdout;
errors print `{"error","hint"}` and exit 1. Default DB path is
`context/memory/portfolio.db`, overridable with `--db`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_portfolio_cli.py
import json
import subprocess
import sys


def _run(tmp_path, *args):
    db = str(tmp_path / "portfolio.db")
    proc = subprocess.run(
        [sys.executable, "skills/balance-sheet/portfolio.py", "--db", db, *args],
        capture_output=True, text=True)
    return proc.returncode, proc.stdout


def test_cli_add_then_networth(tmp_path):
    rc, _ = _run(tmp_path, "add", "--class", "cash", "--label", "Cash", "--value", "1000")
    assert rc == 0
    rc, out = _run(tmp_path, "networth")
    assert rc == 0 and json.loads(out)["net_worth"] == 1000


def test_cli_unknown_class_errors(tmp_path):
    rc, out = _run(tmp_path, "add", "--class", "bogus", "--label", "X", "--value", "1")
    assert rc == 1 and "error" in json.loads(out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_portfolio_cli.py -v`
Expected: FAIL — the script does not exist (non-zero rc, empty stdout).

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
# skills/balance-sheet/portfolio.py
"""Balance-sheet CLI — thin adapter over src.portfolio.engine.

Every subcommand prints JSON to stdout. Errors print {"error","hint"} and
exit 1. The engine owns all logic; this file only parses args and serializes.
Default store: context/memory/portfolio.db (override with --db)."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from src.portfolio import db as pdb       # noqa: E402
from src.portfolio import engine          # noqa: E402

DEFAULT_DB = "context/memory/portfolio.db"


def _kv(pairs: list[str]) -> dict:
    """Parse `key=value` pairs (for `set`)."""
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        out[k] = v
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="portfolio.py", description="Balance-sheet store.")
    p.add_argument("--db", default=DEFAULT_DB)
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("networth", "rollup"):
        sub.add_parser(name)
    sp = sub.add_parser("list"); sp.add_argument("--class", dest="cls"); sp.add_argument("--account")
    sp = sub.add_parser("show"); sp.add_argument("id")
    sp = sub.add_parser("re-equity"); sp.add_argument("property_id")
    sp = sub.add_parser("pnl"); sp.add_argument("--class", dest="cls", default="collectible")
    sp = sub.add_parser("query"); sp.add_argument("sql")
    sp = sub.add_parser("refresh")
    sp = sub.add_parser("render")

    sp = sub.add_parser("add")
    for f in ("class", "label", "ticker", "account", "acquired"):
        sp.add_argument(f"--{f}")
    for f in ("qty", "avg-cost", "cost-basis", "value"):
        sp.add_argument(f"--{f}", type=float)
    sp = sub.add_parser("set"); sp.add_argument("id"); sp.add_argument("pairs", nargs="+")
    sp = sub.add_parser("rm"); sp.add_argument("id")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    pdb.init_db(args.db)
    db = args.db
    try:
        if args.cmd == "networth": out = engine.networth(db)
        elif args.cmd == "rollup": out = engine.rollup(db)
        elif args.cmd == "list": out = engine.list_assets(db, cls=args.cls, account=args.account)
        elif args.cmd == "show": out = engine.show(db, args.id)
        elif args.cmd == "re-equity": out = engine.re_equity(db, args.property_id)
        elif args.cmd == "pnl": out = engine.pnl(db, cls=args.cls)
        elif args.cmd == "query": out = engine.query(db, args.sql)
        elif args.cmd == "refresh": out = engine.refresh(db)
        elif args.cmd == "render": out = {"markdown": engine.render_markdown(db)}
        elif args.cmd == "add":
            fields = {"class": getattr(args, "class"), "label": args.label,
                      "ticker": args.ticker, "account": args.account,
                      "acquired": args.acquired, "qty": args.qty,
                      "avg_cost": args.avg_cost, "cost_basis": args.cost_basis,
                      "value": args.value}
            out = engine.add_asset(db, {k: v for k, v in fields.items() if v is not None})
        elif args.cmd == "set": out = engine.update_asset(db, args.id, _kv(args.pairs))
        elif args.cmd == "rm": out = engine.remove_asset(db, args.id)
        else:
            raise ValueError(f"unknown command {args.cmd!r}")
    except Exception as e:  # noqa: BLE001 — surface as JSON, not a traceback
        print(json.dumps({"error": str(e),
                          "hint": "check the field names / id; see SKILL.md"}))
        return 1
    print(json.dumps(out, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note: `argparse` stores `--avg-cost` as `args.avg_cost` automatically (dashes → underscores).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_portfolio_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skills/balance-sheet/portfolio.py tests/test_portfolio_cli.py
git commit -m "feat(balance-sheet): CLI adapter over the portfolio engine"
```

---

## Task 9: Opt-in tool surface

**Files:**
- Create: `src/tools/portfolio_tool.py`
- Modify: `src/tools/schemas.py`
- Modify: `src/tools/dispatcher.py`
- Test: `tests/test_portfolio_tool.py`

The tool offers the same operations through structured args: a single
`portfolio` tool with an `action` enum + a free-form `args` object, dispatched
to the engine. This keeps one tool (not seven) in the unlocked set.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_portfolio_tool.py
import json
from src.config import AgentConfig
from src.tools.portfolio_tool import exec_portfolio


def _cfg(tmp_path):
    cfg = AgentConfig()
    cfg.portfolio_db = str(tmp_path / "portfolio.db")  # see Step 3 note
    return cfg


def test_tool_add_then_networth(tmp_path):
    cfg = _cfg(tmp_path)
    exec_portfolio({"action": "add",
                    "args": {"class": "cash", "label": "Cash", "value": 1000}}, cfg)
    out = json.loads(exec_portfolio({"action": "networth"}, cfg))
    assert out["net_worth"] == 1000


def test_tool_unknown_action_errors(tmp_path):
    cfg = _cfg(tmp_path)
    out = json.loads(exec_portfolio({"action": "frobnicate"}, cfg))
    assert "error" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_portfolio_tool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.tools.portfolio_tool'`

- [ ] **Step 3: Write minimal implementation**

First the executor:

```python
# src/tools/portfolio_tool.py
"""Opt-in `portfolio` tool — structured-args surface over src.portfolio.engine.

Unlocked by the `balance-sheet` skill (frontmatter `tools: portfolio`). One
tool with an `action` + `args`, so only a single entry joins the unlocked set.
Returns a JSON string (the dispatcher contract)."""
from __future__ import annotations

import json

from src.config import AgentConfig
from src.portfolio import db as pdb
from src.portfolio import engine

_DEFAULT_DB = "context/memory/portfolio.db"

_READ = {
    "networth": lambda db, a: engine.networth(db),
    "rollup": lambda db, a: engine.rollup(db),
    "list": lambda db, a: engine.list_assets(db, cls=a.get("class"), account=a.get("account")),
    "show": lambda db, a: engine.show(db, a["id"]),
    "re_equity": lambda db, a: engine.re_equity(db, a["property_id"]),
    "pnl": lambda db, a: engine.pnl(db, cls=a.get("class", "collectible")),
    "query": lambda db, a: engine.query(db, a["sql"]),
    "render": lambda db, a: {"markdown": engine.render_markdown(db)},
}
_WRITE = {
    "add": lambda db, a: engine.add_asset(db, a),
    "add_liability": lambda db, a: engine.add_liability(db, a),
    "set": lambda db, a: engine.update_asset(db, a["id"], a.get("fields", {})),
    "rm": lambda db, a: engine.remove_asset(db, a["id"]),
    "import_rows": lambda db, a: engine.import_rows(
        db, a["rows"], account=a.get("account"), stated_total=a.get("stated_total")),
    "refresh": lambda db, a: engine.refresh(db),
}


def exec_portfolio(args: dict, config: AgentConfig) -> str:
    db = getattr(config, "portfolio_db", None) or _DEFAULT_DB
    action = args.get("action")
    payload = args.get("args") or {}
    handler = _READ.get(action) or _WRITE.get(action)
    if handler is None:
        return json.dumps({"error": f"unknown action {action!r}",
                           "hint": f"valid: {sorted({*_READ, *_WRITE})}"})
    try:
        pdb.init_db(db)
        return json.dumps(handler(db, payload), default=str)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": str(e), "hint": "check action args; see SKILL.md"})
```

Then register the schema — append to `src/tools/schemas.py` inside the
`_OPT_IN_SCHEMAS` list (before the `for _s in _OPT_IN_SCHEMAS:` loop):

```python
# add as an element of _OPT_IN_SCHEMAS in src/tools/schemas.py
    {
        "type": "function",
        "function": {
            "name": "portfolio",
            "description": (
                "Read and update the owner's balance sheet (assets, "
                "liabilities, net worth). The engine does all math and writes "
                "— never compute a total yourself. Reads: networth, rollup, "
                "list, show, re_equity, pnl, query (read-only SQL), render. "
                "Writes: add, add_liability, set, rm, import_rows (bulk CSV "
                "load with an account-total self-check), refresh (re-price "
                "market holdings). Pass the operation in `action` and its "
                "parameters in `args`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["networth", "rollup", "list", "show", "re_equity",
                                 "pnl", "query", "render", "add", "add_liability",
                                 "set", "rm", "import_rows", "refresh"],
                        "description": "The operation to run.",
                    },
                    "args": {
                        "type": "object",
                        "description": (
                            "Operation parameters, e.g. {class,label,value,...} "
                            "for add; {id,fields:{...}} for set; "
                            "{rows:[...],account,stated_total} for import_rows; "
                            "{sql} for query."
                        ),
                    },
                },
                "required": ["action"],
            },
        },
    },
```

Then route it in `src/tools/dispatcher.py` — add to `_SYNC_EXECUTORS`:

```python
# at top of src/tools/dispatcher.py with the other imports
from src.tools.portfolio_tool import exec_portfolio

# inside the _SYNC_EXECUTORS dict literal, add:
    "portfolio": exec_portfolio,
```

Finally, add a `portfolio_db` field to `AgentConfig` (a plain, unfrozen
`@dataclass` in `src/config.py`). Add this line at the end of the field list,
right after `main_model_supports_vision: bool = False`:

```python
    portfolio_db: str = "context/memory/portfolio.db"
```

Because `AgentConfig` is an unfrozen dataclass, the test's
`cfg.portfolio_db = ...` works even without this field — but adding it makes the
runtime default explicit, and `exec_portfolio` reads it via
`getattr(config, "portfolio_db", None) or _DEFAULT_DB`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_portfolio_tool.py -v`
Expected: PASS

Then verify registration didn't break the schema set:
Run: `python -c "from src.tools.schemas import ALL_TOOL_SCHEMAS; assert 'portfolio' in ALL_TOOL_SCHEMAS; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add src/tools/portfolio_tool.py src/tools/schemas.py src/tools/dispatcher.py src/config.py tests/test_portfolio_tool.py
git commit -m "feat(balance-sheet): opt-in portfolio tool surface over the engine"
```

---

## Task 10: The `balance-sheet` skill + persona allowlist

**Files:**
- Create: `skills/balance-sheet/SKILL.md`
- Modify: `personas/finance/persona.yaml`

- [ ] **Step 1: Write the skill doc**

```markdown
---
name: balance-sheet
description: "Use to track the owner's personal balance sheet — holdings across every asset class (equities, real estate, collectibles, physical/commodities, cash, private/PE), liabilities, cost basis, and net worth. Trigger phrases: 'what's my net worth', 'track my <asset>', 'add this to my portfolio', 'what's my equity in <property>', 'my watch collection value', 'import my brokerage CSV', 'how much <ticker> do I own', 'refresh my values', 'reconcile my accounts'. This is the owner's own book — distinct from financial-analysis (public companies) and investment-memo (theses)."
tools: portfolio
portal_summary: "Track your assets, liabilities, and net worth"
---

# Balance Sheet

Track the owner's assets and liabilities in a structured store and answer
questions about them. **The engine does every calculation and every write** —
you never hand-sum a total or hand-edit the data.

## Data model

The store (`context/memory/portfolio.db`, SQLite) holds `assets` and
`liabilities`. Each asset has a `class` (equity, real_estate, collectible,
physical, cash, private, retirement), a `label`, a current `value`, and —
critically — `cost_basis` and `acquired` (acquisition date). Liabilities
(mortgage, loc, loan) carry a `balance` and may link to an asset (a mortgage →
its property).

## How you reach it

{If the `portfolio` tool is available, call it with an `action` + `args`.}
{Otherwise} run the CLI via bash: `python skills/balance-sheet/portfolio.py <cmd>`.
Both front the same engine. Reads: `networth`, `rollup`, `list`, `show`,
`re-equity <id>`, `pnl`, `query "<SELECT…>"`, `render`. Writes: `add`, `set`,
`rm`, `import_rows`, `refresh`.

## Disciplines (non-negotiable)

- **Never hand-compute a total.** Run `networth` / `rollup` and report what it
  returns. A net worth you summed yourself is a bug.
- **Never hand-edit the DB.** Use `add` / `set` / `rm`.
- **Capture cost basis + acquisition date on every asset.** `add` warns when
  they're missing — ask the owner for them rather than leaving them blank.
- **Pick the right `class`** so the asset lands in the right bucket (physical
  gold is `physical`, not jammed into an `equity` account).
- **Heed the dedup warning.** If `add` says a similar asset already exists,
  confirm with the owner before creating a second record.
- **Bulk-load brokerage exports with `import_rows`.** When the owner uploads a
  CSV (its content is already in your context), map the columns to the schema
  and pass the rows plus the export's stated account total as `stated_total` —
  the engine self-checks the sum and flags a dropped/miscopied row. Do not
  transcribe rows into one-by-one `add` calls.
- **Refresh on demand, not on every read.** Values reflect the last refresh.
  When the owner signals they want current/live/latest figures, run `refresh`
  first, then display. Otherwise show stored values.

## Privacy

These are the owner's real holdings. Never forward specific amounts to a third
party (see the persona guardrails).
```

- [ ] **Step 2: Add to the persona allowlist**

In `personas/finance/persona.yaml`, add `balance-sheet` to the `skills:` list
(near the top, with the other core finance skills):

```yaml
  - financial-analysis
  - investment-memo
  - balance-sheet
```

- [ ] **Step 3: Verify the skill is discoverable**

Run: `python -c "from src.skills import build_skill_manifest; m = build_skill_manifest(['balance-sheet']); assert 'balance-sheet' in m, m; print('ok')"`
Expected: `ok`
(If `build_skill_manifest`'s signature differs, adapt — the goal is to confirm the skill parses and registers. Inspect `src/skills.py` for the actual entry point.)

- [ ] **Step 4: Commit**

```bash
git add skills/balance-sheet/SKILL.md personas/finance/persona.yaml
git commit -m "feat(balance-sheet): SKILL.md + finance persona allowlist entry"
```

---

## Task 11: D — prompt + memory hardening

**Files:**
- Modify: `personas/finance/prompts/10-domain.md`
- Modify: `personas/finance/prompts/20-guardrails.md`
- Modify: `context/memory/README.md`

- [ ] **Step 1: Add the compute-don't-recite rule to the domain prompt**

Append to `personas/finance/prompts/10-domain.md` (after the existing
"Position tracking" bullet / closing paragraph):

```markdown

**Position tracking is tool-backed.** The owner's holdings, cost basis, and
net worth live in the `balance-sheet` capability, not in prose. Never state a
net worth, account total, or portfolio rollup you computed by hand — load
`balance-sheet` and let its engine compute it. A total you summed yourself is
not trustworthy.
```

- [ ] **Step 2: Add verify-before-cite + basis capture to guardrails**

Append to `personas/finance/prompts/20-guardrails.md`:

```markdown
- **Verify before you cite — especially for private/pre-IPO/rumored names.**
  Search snippets can be fabricated (false URLs, invented filings, made-up
  prices). Before stating a filing, price, valuation, or date as fact, fetch
  the underlying source (`web_fetch` the top results) and confirm it exists and
  says what the snippet claimed. If you cannot verify, say so and do not invent
  specifics.
- **Capture cost basis + acquisition date** whenever you record an asset, so
  holding period and the applicable tax rate can be computed later.
```

- [ ] **Step 3: Route asset facts away from prose in the memory README**

In `context/memory/README.md`, under "Routing for the extractor", add a bullet
(and a note that `portfolios.md` is generated):

```markdown
- **About the owner's assets/holdings/liabilities/net worth** → do NOT append
  as prose. These live in the tool-maintained balance-sheet store
  (`portfolio.db`, via the `balance-sheet` capability). `portfolios.md` is a
  generated read-only view — do not hand-edit it.
```

- [ ] **Step 4: Verify prompts still load**

Run: `python -c "from pathlib import Path; [print(p, 'ok') for p in Path('personas/finance/prompts').glob('*.md') if p.read_text()]"`
Expected: each prompt file prints `ok` (non-empty, readable).

- [ ] **Step 5: Commit**

```bash
git add personas/finance/prompts/10-domain.md personas/finance/prompts/20-guardrails.md context/memory/README.md
git commit -m "feat(balance-sheet): D — compute-don't-recite, verify-before-cite, memory routing"
```

---

## Task 12: Migrate the live portfolios.md → portfolio.db

**Files:**
- Create: `scripts/migrate_portfolio.py`

This is a **guided, owner-in-the-loop** migration, not a silent parser — the
live data has known ambiguities (gold double-count, missing watch basis) that
need the owner's input. The script seeds the unambiguous rows; the agent/owner
resolves the rest interactively via the engine afterward.

- [ ] **Step 1: Write the migration script**

```python
#!/usr/bin/env python3
# scripts/migrate_portfolio.py
"""Seed context/memory/portfolio.db from the known-clean rows in the current
portfolios.md. Equity-heavy accounts are better re-imported from the brokerage
CSVs in context/uploads/ via the balance-sheet `import_rows` path; this script
seeds the non-CSV assets (real estate, collectibles, physical, cash, PE) and
the liabilities, then prints the ambiguities for the owner to resolve.

Idempotent-ish: refuses to run if the DB already has assets, to avoid dupes."""
from __future__ import annotations

import sys

from src.portfolio import db as pdb
from src.portfolio import engine

DB = "context/memory/portfolio.db"

# Hand-curated from the current portfolios.md (unambiguous rows only).
ASSETS = [
    {"id": "paladin", "class": "real_estate", "label": "Rental Property",
     "value": 1558400, "cost_basis": 520000, "acquired": "2012"},
    {"id": "carol-ave", "class": "real_estate", "label": "Primary Residence (primary)",
     "value": 3396700, "cost_basis": 2015000, "acquired": "2019"},
    {"id": "gold-physical", "class": "physical", "label": "Physical gold (9 oz)",
     "value": 40295},
    # Collectibles — values known, cost basis/dates MISSING (resolve with owner).
    {"class": "collectible", "label": "Rolex Daytona", "value": 28555},
    {"class": "collectible", "label": "Omega Seamaster Aqua Terra", "value": 4735},
    {"class": "collectible", "label": "Rolex GMT Batman (1)", "value": 15839},
    {"class": "collectible", "label": "Rolex GMT Batman (2)", "value": 16744},
    {"class": "collectible", "label": "Rolex GMT Pepsi", "value": 29506},
    {"class": "collectible", "label": "Rolex Submariner Date", "value": 12569},
]
LIABILITIES = [
    {"id": "carol-mtg", "class": "mortgage", "label": "Primary Residence mortgage",
     "balance": 1214570, "apr": 2.625, "linked_asset": "carol-ave"},
    {"id": "paladin-mtg", "class": "mortgage", "label": "Rental mortgage",
     "balance": 395309, "apr": 3.0, "linked_asset": "paladin"},
    {"id": "loc", "class": "loc", "label": "Line of credit", "balance": 116489, "apr": 7.43},
]

AMBIGUITIES = [
    "GOLD DOUBLE-COUNT: a GLD ETF position (~$221k) and physical gold (~$40k) "
    "both existed — confirm both are real and import the GLD ETF with the "
    "brokerage CSV, not by hand.",
    "WATCH COST BASIS + DATES: all 6 watches lack cost_basis and acquired — "
    "ask the owner and `set` them so holding period / 28% rate can be computed.",
    "BROKERAGE EQUITIES: import accounts -1848, -7942, IRA -5277, 401(k) from "
    "the CSVs in context/uploads/ via `import_rows` (with each account's stated "
    "total as stated_total).",
]


def main() -> int:
    pdb.init_db(DB)
    if engine.list_assets(DB):
        print("refusing to migrate: portfolio.db already has assets")
        return 1
    for a in ASSETS:
        engine.add_asset(DB, a)
    for l in LIABILITIES:
        engine.add_liability(DB, l)
    print("seeded:", engine.networth(DB))
    print("\nRESOLVE WITH OWNER:")
    for i, note in enumerate(AMBIGUITIES, 1):
        print(f"  {i}. {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Dry-run against a temp DB first (don't touch live yet)**

Run:
```bash
python -c "
from src.portfolio import db, engine
import scripts.migrate_portfolio as m
m.DB = '/tmp/mig_test.db'
import os; os.path.exists('/tmp/mig_test.db') and os.remove('/tmp/mig_test.db')
m.main()
"
```
Expected: prints a seeded net worth and the three RESOLVE-WITH-OWNER notes; no error.

- [ ] **Step 3: Run the real migration (owner present)**

Run: `python scripts/migrate_portfolio.py`
Expected: `seeded: {...}` plus the ambiguity list. Then, **with the owner**,
resolve each ambiguity using the CLI/tool: `import_rows` the brokerage CSVs,
`set` the watch cost bases/dates, and confirm/de-dupe gold. Verify the result:
`python skills/balance-sheet/portfolio.py networth` and sanity-check it against
the owner's own number.

- [ ] **Step 4: Regenerate the read-only view**

Run:
```bash
python -c "from src.portfolio import engine; open('context/memory/portfolios.md','w').write(engine.render_markdown('context/memory/portfolio.db'))"
```
Expected: `context/memory/portfolios.md` now carries the generated header.

- [ ] **Step 5: Commit**

```bash
git add scripts/migrate_portfolio.py
git commit -m "feat(balance-sheet): guided migration from portfolios.md to portfolio.db"
```

Note: do **not** commit `context/memory/portfolio.db` or the regenerated
`portfolios.md` if `context/` is gitignored (it typically is — verify with
`git check-ignore context/memory/portfolio.db`). The migration mutates live
memory, which is intentional and local.

---

## Final verification

- [ ] Run the full new suite: `pytest tests/test_portfolio_engine.py tests/test_portfolio_cli.py tests/test_portfolio_tool.py -v` → all PASS.
- [ ] Run the existing tool tests to confirm no regression from the schema/dispatcher change: `pytest tests/test_tools.py -v` → PASS.
- [ ] Confirm the opt-in tool is gated (NOT in the default set):
  `python -c "from src.tools.schemas import get_tool_schemas; assert all(s['function']['name'] != 'portfolio' for s in get_tool_schemas()); print('gated ok')"`

## Hand-off to the C thread

Once the engine lands, the C eval anchor should `from src.portfolio import
engine` (or shell `portfolio.py`) instead of a bespoke `_networth.py`, and C's
fixture becomes a seeded `portfolio.db`. See
`docs/superpowers/specs/2026-06-04-c-thread-coordination-note.md`.

## Fast-follow (separate plan)

Scheduled deterministic `refresh()` coroutine in `run.py`'s TaskGroup
(market-hours-aware, post-close), wired to the surface the experiment selects.
