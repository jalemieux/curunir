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

        base = _slug(fields.get("id") or fields["label"])
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
