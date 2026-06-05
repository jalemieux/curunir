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
