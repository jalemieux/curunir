"""Balance-sheet engine: all reads, writes, refresh, and rendering over the
SQLite store. Synchronous; importable for tests and for the eval anchor.

Every public function takes the DB `path` as its first argument. Reads never
mutate; the only writers are add_asset / update_asset / remove_asset /
import_rows / refresh."""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from datetime import date

from src.portfolio import db as pdb

ASSET_CLASSES = {"equity", "real_estate", "collectible", "physical",
                 "cash", "private", "retirement"}
MARKET_PRICED = {"equity", "physical"}  # crypto tracked as equity (e.g. BTC-USD ticker)
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
        try:
            con.execute(
                f"INSERT INTO assets ({','.join(_COLUMNS)}) "
                f"VALUES ({','.join('?' for _ in _COLUMNS)})",
                tuple(record[c] for c in _COLUMNS),
            )
            con.commit()
        except sqlite3.IntegrityError:
            raise ValueError(
                f"an asset already exists with class={fields['class']!r} "
                f"label={fields['label']!r}")
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
        try:
            cur = con.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
            con.commit()
        except sqlite3.IntegrityError:
            raise ValueError(
                f"cannot remove {asset_id!r}: a liability is linked to it — "
                f"remove or relink the liability first")
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
        value += a["value"] if a["value"] is not None else 0.0
        basis += cb if cb is not None else 0.0
    return {"class": cls, "cost_basis": round(basis, 2), "value": round(value, 2),
            "unrealized": round(value - basis, 2), "items": items}


def query(path: str, sql: str) -> list[dict]:
    """Run an arbitrary read-only SELECT. Opened mode=ro, and the statement
    must begin with SELECT/WITH so a single ATTACH/PRAGMA can't escape the
    read-only contract."""
    head = sql.lstrip().lstrip("(").upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        raise ValueError("query() accepts only read-only SELECT/WITH statements")
    con = pdb.connect(path, readonly=True)
    try:
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


def import_rows(path: str, rows: list[dict], account: str | None = None,
                stated_total: float | None = None, tolerance: float = 1.0) -> dict:
    """Bulk-insert mapped rows (e.g. from a brokerage CSV the LLM parsed).
    Stamps `account` on each. If `stated_total` is given (the export's account
    value), compares it to the summed imported value and reports a mismatch —
    the deterministic catch for a dropped/miscopied row."""
    seen = set()
    existing_pairs = {(a["class"], a["label"]) for a in list_assets(path)}
    for idx, row in enumerate(rows):
        miss = [k for k in _REQUIRED if row.get(k) in (None, "")]
        if miss:
            raise ValueError(f"row {idx} ({row.get('label','?')}): missing {', '.join(miss)}")
        if row["class"] not in ASSET_CLASSES:
            raise ValueError(f"row {idx} ({row.get('label','?')}): unknown class {row['class']!r}")
        pair = (row["class"], row["label"])
        if pair in seen or pair in existing_pairs:
            raise ValueError(f"row {idx}: duplicate (class={row['class']!r}, "
                             f"label={row['label']!r}) within the batch or already stored")
        seen.add(pair)

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


# --- trade ledger -----------------------------------------------------------

TRADABLE = {"equity", "physical"}  # qty-bearing classes the ledger accepts
_TRADE_COLUMNS = ("id", "trade_date", "side", "ticker", "qty", "price", "fees",
                  "asset_id", "account", "cost_basis_sold", "proceeds",
                  "realized_pnl", "long_term", "note", "created_at")


def _insert_trade(con: sqlite3.Connection, rec: dict) -> str:
    """Insert one trade row with a deduped slug id. Caller commits."""
    base = _slug(rec.get("id") or
                 f"t-{rec['trade_date']}-{rec['ticker']}-{rec['side']}")
    new_id, i = base, 2
    existing = {r["id"] for r in con.execute("SELECT id FROM trades")}
    while new_id in existing:
        new_id, i = f"{base}-{i}", i + 1
    rec = {**rec, "id": new_id,
           "created_at": rec.get("created_at") or date.today().isoformat()}
    con.execute(
        f"INSERT INTO trades ({','.join(_TRADE_COLUMNS)}) "
        f"VALUES ({','.join('?' for _ in _TRADE_COLUMNS)})",
        tuple(rec.get(c) for c in _TRADE_COLUMNS))
    return new_id


def record_buy(path: str, fields: dict) -> dict:
    """Record a buy: mint a NEW asset lot and append a buy trade. `fields`:
    ticker, qty, price, trade_date (required); class (default equity), fees,
    account, label, note (optional). Cost basis = qty*price + fees."""
    for k in ("ticker", "qty", "price", "trade_date"):
        if fields.get(k) in (None, ""):
            raise ValueError(f"missing required trade field: {k}")
    cls = fields.get("class", "equity")
    if cls not in TRADABLE:
        raise ValueError(f"class {cls!r} is not tradable; "
                         f"the ledger handles qty-bearing classes only: "
                         f"{sorted(TRADABLE)}")
    qty = float(fields["qty"]); price = float(fields["price"])
    fees = float(fields.get("fees") or 0.0)
    if qty <= 0 or price < 0:
        raise ValueError("qty must be > 0 and price >= 0")
    trade_date = fields["trade_date"]
    ticker = fields["ticker"]
    basis = round(qty * price + fees, 2)
    lot = add_asset(path, {
        "class": cls,
        "label": fields.get("label") or f"{ticker} {trade_date}",
        "ticker": ticker, "qty": qty, "avg_cost": price,
        "cost_basis": basis, "value": round(qty * price, 2),
        "value_asof": trade_date, "acquired": trade_date,
        "account": fields.get("account"),
    })
    con = pdb.connect(path)
    try:
        trade_id = _insert_trade(con, {
            "trade_date": trade_date, "side": "buy", "ticker": ticker,
            "qty": qty, "price": price, "fees": fees, "asset_id": lot["id"],
            "account": fields.get("account"), "note": fields.get("note"),
        })
        con.commit()
    finally:
        con.close()
    return {"trade_id": trade_id, "asset_id": lot["id"],
            "lot": show(path, lot["id"]), "warnings": lot["warnings"]}


def record_sell(path: str, fields: dict) -> dict:
    """Record a sell against a named lot (specific-lot): compute realized P/L,
    draw the lot down (delete at zero qty), and append a sell trade. `fields`:
    asset_id, qty, price, trade_date (required); fees, note (optional)."""
    for k in ("asset_id", "qty", "price", "trade_date"):
        if fields.get(k) in (None, ""):
            raise ValueError(f"missing required trade field: {k}")
    lot = show(path, fields["asset_id"])  # raises KeyError if absent
    if lot["class"] not in TRADABLE:
        raise ValueError(f"lot {lot['id']!r} is class {lot['class']!r}, not "
                         f"tradable; the ledger handles {sorted(TRADABLE)} only")
    if lot.get("qty") in (None, ""):
        raise ValueError(f"lot {lot['id']!r} has no qty — cannot sell shares")
    if lot.get("cost_basis") in (None, ""):
        raise ValueError(f"lot {lot['id']!r} has no cost_basis — set one with "
                         f"`set` before recording a sell (realized P/L needs it)")

    sold = float(fields["qty"]); price = float(fields["price"])
    fees = float(fields.get("fees") or 0.0)
    lot_qty = float(lot["qty"])
    if sold <= 0:
        raise ValueError("qty must be > 0")
    if sold > lot_qty:
        raise ValueError(f"cannot sell {sold} shares from lot {lot['id']!r} "
                         f"holding {lot_qty} — name a different lot or split the sell")

    trade_date = fields["trade_date"]
    per_share_basis = float(lot["cost_basis"]) / lot_qty
    cost_basis_sold = round(sold * per_share_basis, 2)
    proceeds = round(sold * price - fees, 2)
    realized = round(proceeds - cost_basis_sold, 2)
    long_term = None
    if lot.get("acquired"):
        long_term = _years_between(lot["acquired"], trade_date) >= 1.0

    remaining = round(lot_qty - sold, 8)
    lot_closed = remaining == 0
    if lot_closed:
        remove_asset(path, lot["id"])
    else:
        update_asset(path, lot["id"], {
            "qty": remaining,
            "cost_basis": round(float(lot["cost_basis"]) - cost_basis_sold, 2),
            "value": round(remaining * price, 2),
            "value_asof": trade_date,
        })

    con = pdb.connect(path)
    try:
        trade_id = _insert_trade(con, {
            "trade_date": trade_date, "side": "sell", "ticker": lot["ticker"],
            "qty": sold, "price": price, "fees": fees, "asset_id": lot["id"],
            "account": lot.get("account"), "cost_basis_sold": cost_basis_sold,
            "proceeds": proceeds, "realized_pnl": realized,
            "long_term": (1 if long_term else 0) if long_term is not None else None,
            "note": fields.get("note"),
        })
        con.commit()
    finally:
        con.close()
    return {"trade_id": trade_id, "realized_pnl": realized,
            "cost_basis_sold": cost_basis_sold, "proceeds": proceeds,
            "long_term": long_term, "remaining_qty": remaining,
            "lot_closed": lot_closed}


def trade_history(path: str, ticker: str | None = None, account: str | None = None,
                  side: str | None = None, since: str | None = None) -> list[dict]:
    """Filtered trade ledger, newest-first."""
    sql = "SELECT * FROM trades"
    clauses, params = [], []
    if ticker:
        clauses.append("ticker = ?"); params.append(ticker)
    if account:
        clauses.append("account = ?"); params.append(account)
    if side:
        clauses.append("side = ?"); params.append(side)
    if since:
        clauses.append("trade_date >= ?"); params.append(since)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY trade_date DESC, created_at DESC, id DESC"
    con = pdb.connect(path)
    try:
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def realized_pnl(path: str, ticker: str | None = None, account: str | None = None,
                 year: int | None = None) -> dict:
    """Realized P/L over sell trades, split short/long-term. Filterable by
    ticker, account, and calendar year (of trade_date)."""
    short = long = 0.0
    trades = []
    for t in trade_history(path, ticker=ticker, account=account, side="sell"):
        if year is not None and not str(t["trade_date"]).startswith(str(year)):
            continue
        r = t.get("realized_pnl") or 0.0
        if t.get("long_term"):
            long += r
        else:
            short += r
        trades.append(t)
    return {"short_term": round(short, 2), "long_term": round(long, 2),
            "total": round(short + long, 2), "count": len(trades),
            "trades": trades}


def _yfin_quote(ticker: str) -> float:
    """Default quoter: shell out to the yfinance CLI. Returns the last price."""
    import os
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    proc = subprocess.run([sys.executable, "skills/yfinance/yfin.py", "quote", ticker],
                          capture_output=True, text=True, timeout=30, cwd=root)
    if proc.returncode != 0:
        raise ValueError(f"yfin quote failed for {ticker}: "
                         f"{proc.stdout.strip() or proc.stderr.strip()}")
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
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{a['ticker']}: {exc}")
            continue
        update_asset(path, a["id"],
                     {"value": round(float(a["qty"]) * price, 2), "value_asof": today})
        repriced += 1
    return {"repriced": repriced, "errors": errors, "asof": today}


def render_markdown(path: str) -> str:
    """Return a markdown view of the store, regenerated from the DB on demand.
    A caller (the migration step, or a refresh) writes the returned string to
    `context/memory/portfolios.md` — the generated, read-only view that is
    never hand-edited. This function itself writes no file."""
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
