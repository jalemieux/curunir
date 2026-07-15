"""Broker↔local reconciliation — diff, never a silent overwrite.

``broker_diff`` compares normalized broker positions against local assets keyed
on ``(account, ticker)``. Because broker positions are aggregate and the local
model is specific-lot, broker qty is compared against the *sum* of local lots.
Positions bucket into:

- **matched**       — qty aligns and value is already fresh
- **price_stale**   — qty aligns but local value differs from the broker's
- **qty_drift**     — qty differs (a real buy/sell the human must confirm)
- **missing_local** — the broker holds a ticker we don't (a new position)
- **missing_remote**— we hold a ticker the broker doesn't report

``broker_apply`` is conservative: it re-prices matched/stale lots (value =
qty·price, value_asof = position as-of) and inserts brand-new tickers as one
lot tagged ``extra.source=<adapter>``. Qty drift and missing-remote are only
reported — resolving them means real trades, which stay a human decision
(consistent with "tool-backed, never prose"). Apply is idempotent: a second
run re-prices the just-inserted lot instead of re-inserting it.

All local reads/writes go through ``engine`` (``list_assets`` / ``add_asset``
/ ``update_asset``) — no new query logic, so this can't drift from the store.
"""
from __future__ import annotations

from collections import defaultdict

from src.portfolio import engine
from src.portfolio.brokers.base import BrokerPosition

#: Qty is treated as aligned within this absolute tolerance (fractional shares).
_QTY_TOL = 1e-6
#: Value/market-value is treated as fresh within this absolute tolerance ($).
_VALUE_TOL = 1.0


def _local_by_account_ticker(path: str) -> dict[tuple[str, str], list[dict]]:
    """Group local qty-bearing lots by ``(account, ticker)``."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for a in engine.list_assets(path):
        ticker = a.get("ticker")
        if not ticker:
            continue
        groups[(a.get("account") or "", ticker)].append(a)
    return groups


def broker_diff(path: str, positions: list[BrokerPosition]) -> dict:
    """Bucket ``positions`` against the local store. Pure read; never writes."""
    local = _local_by_account_ticker(path)
    seen_keys: set[tuple[str, str]] = set()

    matched, price_stale, qty_drift, missing_local = [], [], [], []
    for p in positions:
        key = (p.account_id or "", p.ticker)
        seen_keys.add(key)
        lots = local.get(key, [])
        if not lots:
            missing_local.append({
                "ticker": p.ticker, "account": p.account_id,
                "broker_qty": p.qty, "price": p.price,
                "market_value": p.market_value, "cost_basis": p.cost_basis,
                "as_of": p.as_of,
            })
            continue
        local_qty = sum(float(a.get("qty") or 0) for a in lots)
        local_value = sum(float(a.get("value") or 0) for a in lots)
        row = {
            "ticker": p.ticker, "account": p.account_id,
            "local_qty": round(local_qty, 8), "broker_qty": p.qty,
            "local_value": round(local_value, 2), "market_value": p.market_value,
            "price": p.price, "as_of": p.as_of,
            "lot_ids": [a["id"] for a in lots],
        }
        if abs(local_qty - float(p.qty)) > _QTY_TOL:
            qty_drift.append(row)
        elif (p.market_value is not None
              and abs(local_value - float(p.market_value)) > _VALUE_TOL):
            price_stale.append(row)
        else:
            matched.append(row)

    missing_remote = []
    for key, lots in local.items():
        if key in seen_keys:
            continue
        account, ticker = key
        missing_remote.append({
            "ticker": ticker, "account": account,
            "local_qty": round(sum(float(a.get("qty") or 0) for a in lots), 8),
            "local_value": round(sum(float(a.get("value") or 0) for a in lots), 2),
            "lot_ids": [a["id"] for a in lots],
        })

    return {
        "matched": matched,
        "price_stale": price_stale,
        "qty_drift": qty_drift,
        "missing_local": missing_local,
        "missing_remote": missing_remote,
    }


def broker_apply(path: str, positions: list[BrokerPosition],
                 source: str = "broker") -> dict:
    """Apply the conservative subset of a diff and return the report.

    Updates matched/stale lots (re-price), inserts missing-local tickers as one
    ``source``-tagged lot, and only *reports* qty drift and missing-remote.
    """
    diff = broker_diff(path, positions)
    pos_by_key = {(p.account_id or "", p.ticker): p for p in positions}

    updated, inserted = [], []

    # Re-price matched + price-stale lots: value = lot.qty * broker price.
    for row in diff["matched"] + diff["price_stale"]:
        p = pos_by_key[(row["account"] or "", row["ticker"])]
        for lot in [engine.show(path, lid) for lid in row["lot_ids"]]:
            new_value = _lot_value(lot, p)
            fields = {"value": new_value}
            if p.as_of:
                fields["value_asof"] = p.as_of
            engine.update_asset(path, lot["id"], fields)
        updated.append({"ticker": row["ticker"], "account": row["account"],
                        "lot_ids": row["lot_ids"], "market_value": p.market_value})

    # Insert brand-new tickers as one aggregate, sourced lot.
    for row in diff["missing_local"]:
        p = pos_by_key[(row["account"] or "", row["ticker"])]
        value = (p.market_value if p.market_value is not None
                 else (float(p.qty) * float(p.price)) if p.price is not None
                 else 0.0)
        res = engine.add_asset(path, {
            "class": "equity",
            "label": f"{p.ticker} ({source})",
            "ticker": p.ticker, "qty": p.qty,
            "cost_basis": p.cost_basis, "value": round(value, 2),
            "value_asof": p.as_of, "acquired": p.as_of,
            "account": p.account_id, "extra": {"source": source},
        })
        inserted.append({"ticker": p.ticker, "account": p.account_id,
                         "id": res["id"]})

    return {
        "source": source,
        "applied": {"updated": updated, "inserted": inserted},
        "skipped": {
            "qty_drift": diff["qty_drift"],
            "missing_remote": diff["missing_remote"],
        },
    }


def _lot_value(lot: dict, position: BrokerPosition) -> float:
    """New value for a lot given the broker position.

    Prefer per-share re-price (qty·price) so multi-lot positions stay
    consistent and sum to the aggregate; fall back to distributing the
    aggregate market value proportionally by qty when no price is given.
    """
    lot_qty = float(lot.get("qty") or 0)
    if position.price is not None:
        return round(lot_qty * float(position.price), 2)
    if position.market_value is not None and position.qty:
        return round(float(position.market_value) * (lot_qty / float(position.qty)), 2)
    return float(lot.get("value") or 0)
