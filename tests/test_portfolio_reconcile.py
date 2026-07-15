"""Tests for the broker↔local reconciliation layer (src.portfolio.reconcile).

Reconcile never overwrites silently: it buckets broker positions against local
lots (matched / price-stale / qty-drift / missing-local / missing-remote) and
``broker_apply`` only re-prices matched holdings and inserts brand-new tickers
— qty drift and locally-missing positions are reported, not written.
"""
from __future__ import annotations

import pytest

from src.portfolio import db as pdb
from src.portfolio import engine, reconcile
from src.portfolio.brokers.base import BrokerPosition


def _fresh(tmp_path):
    path = str(tmp_path / "portfolio.db")
    pdb.init_db(path)
    return path


def _seed_lot(path, ticker, qty, value, account="etrade-brokerage", cost=None):
    return engine.add_asset(path, {
        "class": "equity", "label": f"{ticker} lot", "ticker": ticker,
        "qty": qty, "value": value, "cost_basis": cost, "account": account,
    })


def _pos(ticker, qty, *, price=None, market_value=None, cost_basis=None,
         account="etrade-brokerage", as_of="2026-07-15"):
    return BrokerPosition(
        account_id=account, ticker=ticker, qty=qty, price=price,
        market_value=market_value, cost_basis=cost_basis, as_of=as_of,
    )


# --- diff buckets -----------------------------------------------------------

def test_diff_matched_when_qty_and_value_align(tmp_path):
    path = _fresh(tmp_path)
    _seed_lot(path, "AAPL", 10, 1500.0)
    diff = reconcile.broker_diff(
        path, [_pos("AAPL", 10, price=150.0, market_value=1500.0)])
    assert [r["ticker"] for r in diff["matched"]] == ["AAPL"]
    assert diff["price_stale"] == []
    assert diff["qty_drift"] == []
    assert diff["missing_local"] == []
    assert diff["missing_remote"] == []


def test_diff_price_stale_when_qty_matches_but_value_differs(tmp_path):
    path = _fresh(tmp_path)
    _seed_lot(path, "AAPL", 10, 1200.0)  # local value stale
    diff = reconcile.broker_diff(
        path, [_pos("AAPL", 10, price=150.0, market_value=1500.0)])
    assert [r["ticker"] for r in diff["price_stale"]] == ["AAPL"]
    assert diff["matched"] == []


def test_diff_qty_drift_across_multilot_position(tmp_path):
    path = _fresh(tmp_path)
    # Two local lots summing to 8 shares; broker reports 10 → drift.
    _seed_lot(path, "AAPL", 5, 750.0)
    engine.add_asset(path, {
        "class": "equity", "label": "AAPL lot 2", "ticker": "AAPL",
        "qty": 3, "value": 450.0, "account": "etrade-brokerage",
    })
    diff = reconcile.broker_diff(
        path, [_pos("AAPL", 10, price=150.0, market_value=1500.0)])
    assert len(diff["qty_drift"]) == 1
    row = diff["qty_drift"][0]
    assert row["ticker"] == "AAPL"
    assert row["local_qty"] == 8
    assert row["broker_qty"] == 10


def test_diff_missing_local_when_broker_has_unknown_ticker(tmp_path):
    path = _fresh(tmp_path)
    diff = reconcile.broker_diff(
        path, [_pos("TSLA", 4, price=250.0, market_value=1000.0, cost_basis=900.0)])
    assert [r["ticker"] for r in diff["missing_local"]] == ["TSLA"]


def test_diff_missing_remote_when_local_ticker_absent_from_broker(tmp_path):
    path = _fresh(tmp_path)
    _seed_lot(path, "AAPL", 10, 1500.0)
    _seed_lot(path, "GOOG", 2, 300.0)
    diff = reconcile.broker_diff(
        path, [_pos("AAPL", 10, price=150.0, market_value=1500.0)])
    assert [r["ticker"] for r in diff["missing_remote"]] == ["GOOG"]


def test_diff_ignores_local_lots_from_other_accounts(tmp_path):
    path = _fresh(tmp_path)
    _seed_lot(path, "AAPL", 10, 1500.0, account="fidelity")
    # Broker (etrade) has AAPL, local AAPL is a different account → missing_local
    diff = reconcile.broker_diff(
        path, [_pos("AAPL", 10, price=150.0, market_value=1500.0)])
    assert [r["ticker"] for r in diff["missing_local"]] == ["AAPL"]


# --- apply ------------------------------------------------------------------

def test_apply_reprices_matched_and_stale(tmp_path):
    path = _fresh(tmp_path)
    res = _seed_lot(path, "AAPL", 10, 1200.0)
    lot_id = res["id"]
    report = reconcile.broker_apply(
        path, [_pos("AAPL", 10, price=150.0, market_value=1500.0)],
        source="etrade")
    assert report["applied"]["updated"]
    lot = engine.show(path, lot_id)
    assert lot["value"] == 1500.0  # 10 * 150
    assert lot["value_asof"] == "2026-07-15"


def test_apply_inserts_new_ticker_tagged_source(tmp_path):
    path = _fresh(tmp_path)
    report = reconcile.broker_apply(
        path, [_pos("TSLA", 4, price=250.0, market_value=1000.0, cost_basis=900.0)],
        source="etrade")
    assert [r["ticker"] for r in report["applied"]["inserted"]] == ["TSLA"]
    lots = [a for a in engine.list_assets(path) if a["ticker"] == "TSLA"]
    assert len(lots) == 1
    assert lots[0]["qty"] == 4
    assert lots[0]["cost_basis"] == 900.0
    import json as _json
    extra = _json.loads(lots[0]["extra"]) if lots[0]["extra"] else {}
    assert extra.get("source") == "etrade"


def test_apply_does_not_touch_qty_drift_or_missing_local_reporting(tmp_path):
    path = _fresh(tmp_path)
    res = _seed_lot(path, "AAPL", 8, 1200.0)
    lot_id = res["id"]
    report = reconcile.broker_apply(
        path, [_pos("AAPL", 10, price=150.0, market_value=1500.0)],
        source="etrade")
    # Qty drift is only reported; the local lot qty/value is left as-is.
    assert [r["ticker"] for r in report["skipped"]["qty_drift"]] == ["AAPL"]
    lot = engine.show(path, lot_id)
    assert lot["qty"] == 8
    assert lot["value"] == 1200.0


def test_apply_reports_missing_remote_without_touching_it(tmp_path):
    path = _fresh(tmp_path)
    _seed_lot(path, "AAPL", 10, 1500.0)
    _seed_lot(path, "GOOG", 2, 300.0)
    report = reconcile.broker_apply(
        path, [_pos("AAPL", 10, price=150.0, market_value=1500.0)],
        source="etrade")
    assert [r["ticker"] for r in report["skipped"]["missing_remote"]] == ["GOOG"]


def test_apply_is_idempotent(tmp_path):
    path = _fresh(tmp_path)
    positions = [_pos("TSLA", 4, price=250.0, market_value=1000.0, cost_basis=900.0)]
    reconcile.broker_apply(path, positions, source="etrade")
    reconcile.broker_apply(path, positions, source="etrade")
    lots = [a for a in engine.list_assets(path) if a["ticker"] == "TSLA"]
    assert len(lots) == 1  # not double-inserted
    assert lots[0]["value"] == 1000.0
