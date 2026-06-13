import sqlite3

import pytest

from src.portfolio import db as pdb
from src.portfolio import engine


def _fresh(tmp_path):
    path = str(tmp_path / "portfolio.db")
    pdb.init_db(path)
    return path


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
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO assets(id,class,label,value) VALUES('x','cash','x',1)")
    con.close()


def test_add_asset_assigns_id_and_persists(tmp_path):
    path = _fresh(tmp_path)
    res = engine.add_asset(path, {"class": "cash", "label": "Checking", "value": 1000})
    assert res["id"]
    rows = engine.list_assets(path)
    assert len(rows) == 1 and rows[0]["label"] == "Checking"


def test_add_asset_requires_class_label_value(tmp_path):
    path = _fresh(tmp_path)
    with pytest.raises(ValueError):
        engine.add_asset(path, {"class": "cash", "label": "x"})


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
    with pytest.raises(KeyError):
        engine.update_asset(path, "nope", {"value": 1})


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
    assert r["real_estate_equity"] == 600000
    assert r["equities"] == 200000
    assert r["collectibles"] == 50000
    assert r["debt"] == 400000
    assert r["net_worth"] == 850000


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
    assert p["items"][0]["long_term"] is True


def test_query_is_readonly(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 5})
    rows = engine.query(path, "SELECT label, value FROM assets")
    assert rows == [{"label": "Cash", "value": 5}]
    with pytest.raises(Exception):
        engine.query(path, "DELETE FROM assets")


def test_import_rows_inserts_and_self_checks_ok(tmp_path):
    path = _fresh(tmp_path)
    rows = [
        {"class": "equity", "label": "VOO", "ticker": "VOO", "qty": 10,
         "cost_basis": 3000, "value": 7000},
        {"class": "equity", "label": "GLD", "ticker": "GLD", "qty": 5,
         "cost_basis": 1600, "value": 2200},
    ]
    res = engine.import_rows(path, rows, account="brokerage-7942", stated_total=9200)
    assert res["imported"] == 2
    assert res["self_check"]["ok"] is True
    assert len(engine.list_assets(path, account="brokerage-7942")) == 2


def test_import_rows_flags_total_mismatch(tmp_path):
    path = _fresh(tmp_path)
    rows = [{"class": "equity", "label": "VOO", "value": 7000}]
    res = engine.import_rows(path, rows, account="brokerage-7942", stated_total=9200)
    assert res["self_check"]["ok"] is False
    assert "9200" in res["self_check"]["detail"] or "9,200" in res["self_check"]["detail"]


def test_refresh_reprices_only_market_priced(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "equity", "label": "VOO", "ticker": "VOO",
                            "qty": 10, "value": 7000})
    engine.add_asset(path, {"class": "collectible", "label": "Watch", "value": 5000})

    def fake_quote(ticker):
        return {"VOO": 800.0}[ticker]

    res = engine.refresh(path, quoter=fake_quote)
    assert res["repriced"] == 1
    assert engine.show(path, "voo")["value"] == 8000
    assert engine.show(path, "watch")["value"] == 5000


def test_render_markdown_has_networth_and_warning(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 1000})
    md = engine.render_markdown(path)
    assert "do not hand-edit" in md.lower()
    assert "Net Worth" in md and "1,000" in md


def test_add_asset_exact_duplicate_raises(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 1})
    with pytest.raises(ValueError):
        engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 2})


def test_remove_asset_linked_liability_raises(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"id": "house", "class": "real_estate", "label": "House", "value": 1000000})
    engine.add_liability(path, {"class": "mortgage", "label": "mtg", "balance": 400000, "linked_asset": "house"})
    with pytest.raises(ValueError):
        engine.remove_asset(path, "house")


def test_import_rows_bad_row_aborts_before_any_insert(tmp_path):
    path = _fresh(tmp_path)
    rows = [{"class": "equity", "label": "VOO", "value": 7000},
            {"class": "bogus", "label": "X", "value": 1}]
    with pytest.raises(ValueError):
        engine.import_rows(path, rows)
    assert engine.list_assets(path) == []  # all-or-nothing


def test_query_rejects_non_select(tmp_path):
    path = _fresh(tmp_path)
    with pytest.raises(ValueError):
        engine.query(path, "ATTACH DATABASE '/tmp/evil.db' AS evil")


def test_import_rows_duplicate_label_aborts_before_insert(tmp_path):
    path = _fresh(tmp_path)
    rows = [{"class": "equity", "label": "VOO", "value": 100},
            {"class": "equity", "label": "VOO", "value": 200}]
    with pytest.raises(ValueError):
        engine.import_rows(path, rows)
    assert engine.list_assets(path) == []  # truly all-or-nothing


# --- trade ledger -----------------------------------------------------------

def test_trades_table_exists(tmp_path):
    path = _fresh(tmp_path)
    con = sqlite3.connect(path)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "trades" in names


def test_record_buy_creates_new_lot_and_trade(tmp_path):
    path = _fresh(tmp_path)
    res = engine.record_buy(path, {"ticker": "SPCX", "qty": 100, "price": 150,
                                   "trade_date": "2026-01-10", "account": "ira"})
    lot = engine.show(path, res["asset_id"])
    assert lot["qty"] == 100
    assert lot["cost_basis"] == 15000          # 100 * 150
    assert lot["value"] == 15000
    assert lot["acquired"] == "2026-01-10"
    assert lot["account"] == "ira"
    trades = engine.trade_history(path)
    assert len(trades) == 1 and trades[0]["side"] == "buy"
    assert trades[0]["realized_pnl"] is None


def test_record_buy_includes_fees_in_basis(tmp_path):
    path = _fresh(tmp_path)
    res = engine.record_buy(path, {"ticker": "VOO", "qty": 10, "price": 500,
                                   "trade_date": "2026-02-01", "fees": 7})
    assert engine.show(path, res["asset_id"])["cost_basis"] == 5007


def test_record_buy_rejects_non_qty_class(tmp_path):
    path = _fresh(tmp_path)
    with pytest.raises(ValueError):
        engine.record_buy(path, {"ticker": "X", "qty": 1, "price": 1,
                                 "trade_date": "2026-01-01", "class": "collectible"})


def test_record_buy_each_buy_is_a_new_lot(tmp_path):
    path = _fresh(tmp_path)
    a = engine.record_buy(path, {"ticker": "VOO", "qty": 10, "price": 500,
                                 "trade_date": "2026-01-01"})["asset_id"]
    b = engine.record_buy(path, {"ticker": "VOO", "qty": 5, "price": 520,
                                 "trade_date": "2026-03-01"})["asset_id"]
    assert a != b
    assert len(engine.list_assets(path, cls="equity")) == 2


def test_record_sell_decrements_lot_and_computes_realized(tmp_path):
    path = _fresh(tmp_path)
    lot = engine.record_buy(path, {"ticker": "SPCX", "qty": 100, "price": 150,
                                   "trade_date": "2024-01-10"})["asset_id"]
    res = engine.record_sell(path, {"asset_id": lot, "qty": 40, "price": 170,
                                    "trade_date": "2026-06-13"})
    # proceeds 40*170=6800; basis sold 40*150=6000; realized 800
    assert res["realized_pnl"] == 800
    assert res["remaining_qty"] == 60
    assert res["lot_closed"] is False
    assert res["long_term"] is True            # held >1yr
    remaining = engine.show(path, lot)
    assert remaining["qty"] == 60
    assert remaining["cost_basis"] == 9000     # 15000 - 6000


def test_record_sell_closes_lot_at_zero(tmp_path):
    path = _fresh(tmp_path)
    lot = engine.record_buy(path, {"ticker": "GLD", "qty": 5, "price": 200,
                                   "trade_date": "2026-01-01"})["asset_id"]
    res = engine.record_sell(path, {"asset_id": lot, "qty": 5, "price": 220,
                                    "trade_date": "2026-02-01"})
    assert res["lot_closed"] is True
    assert res["remaining_qty"] == 0
    with pytest.raises(KeyError):
        engine.show(path, lot)
    # trade history survives the closed lot
    assert len(engine.trade_history(path, side="sell")) == 1


def test_record_sell_short_term_flag(tmp_path):
    path = _fresh(tmp_path)
    lot = engine.record_buy(path, {"ticker": "AAPL", "qty": 10, "price": 100,
                                   "trade_date": "2026-01-01"})["asset_id"]
    res = engine.record_sell(path, {"asset_id": lot, "qty": 10, "price": 120,
                                    "trade_date": "2026-06-01"})
    assert res["long_term"] is False


def test_record_sell_rejects_oversell(tmp_path):
    path = _fresh(tmp_path)
    lot = engine.record_buy(path, {"ticker": "VOO", "qty": 5, "price": 500,
                                   "trade_date": "2026-01-01"})["asset_id"]
    with pytest.raises(ValueError):
        engine.record_sell(path, {"asset_id": lot, "qty": 6, "price": 510,
                                  "trade_date": "2026-02-01"})


def test_record_sell_unknown_lot_raises(tmp_path):
    path = _fresh(tmp_path)
    with pytest.raises(KeyError):
        engine.record_sell(path, {"asset_id": "nope", "qty": 1, "price": 1,
                                  "trade_date": "2026-01-01"})


def test_record_sell_requires_cost_basis(tmp_path):
    path = _fresh(tmp_path)
    aid = engine.add_asset(path, {"class": "equity", "label": "MYST", "ticker": "MYST",
                                  "qty": 10, "value": 1000})["id"]
    with pytest.raises(ValueError):
        engine.record_sell(path, {"asset_id": aid, "qty": 1, "price": 100,
                                  "trade_date": "2026-01-01"})


def test_trade_history_filters(tmp_path):
    path = _fresh(tmp_path)
    a = engine.record_buy(path, {"ticker": "VOO", "qty": 10, "price": 500,
                                 "trade_date": "2026-01-01", "account": "ira"})["asset_id"]
    engine.record_buy(path, {"ticker": "GLD", "qty": 5, "price": 200,
                             "trade_date": "2026-01-02", "account": "taxable"})
    engine.record_sell(path, {"asset_id": a, "qty": 2, "price": 520,
                              "trade_date": "2026-02-01"})
    assert len(engine.trade_history(path, ticker="VOO")) == 2
    assert len(engine.trade_history(path, side="sell")) == 1
    assert len(engine.trade_history(path, account="taxable")) == 1


def test_realized_pnl_splits_short_and_long_term(tmp_path):
    path = _fresh(tmp_path)
    lt = engine.record_buy(path, {"ticker": "AAA", "qty": 10, "price": 100,
                                  "trade_date": "2024-01-01"})["asset_id"]
    st = engine.record_buy(path, {"ticker": "BBB", "qty": 10, "price": 100,
                                  "trade_date": "2026-01-01"})["asset_id"]
    engine.record_sell(path, {"asset_id": lt, "qty": 10, "price": 150,
                              "trade_date": "2026-06-01"})   # +500 long
    engine.record_sell(path, {"asset_id": st, "qty": 10, "price": 90,
                              "trade_date": "2026-06-01"})   # -100 short
    r = engine.realized_pnl(path)
    assert r["long_term"] == 500
    assert r["short_term"] == -100
    assert r["total"] == 400


def test_realized_pnl_filters_by_year(tmp_path):
    path = _fresh(tmp_path)
    a = engine.record_buy(path, {"ticker": "AAA", "qty": 10, "price": 100,
                                 "trade_date": "2024-01-01"})["asset_id"]
    engine.record_sell(path, {"asset_id": a, "qty": 5, "price": 150,
                              "trade_date": "2025-06-01"})   # +250 in 2025
    engine.record_sell(path, {"asset_id": a, "qty": 5, "price": 200,
                              "trade_date": "2026-06-01"})   # +500 in 2026
    assert engine.realized_pnl(path, year=2025)["total"] == 250
    assert engine.realized_pnl(path, year=2026)["total"] == 500


def test_unrealized_sums_assets_with_cost_basis(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "equity", "label": "Acme",
                            "value": 1000, "cost_basis": 600})
    engine.add_asset(path, {"class": "collectible", "label": "Watch",
                            "value": 300, "cost_basis": 500})  # at a loss
    r = engine.unrealized(path)
    assert r["cost_basis"] == 1100
    assert r["value"] == 1300
    assert r["unrealized"] == 200


def test_unrealized_excludes_assets_without_cost_basis(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "equity", "label": "Acme",
                            "value": 1000, "cost_basis": 600})
    engine.add_asset(path, {"class": "cash", "label": "Checking", "value": 5000})
    r = engine.unrealized(path)
    # Cash (no cost_basis) is excluded from BOTH sides.
    assert r["cost_basis"] == 600
    assert r["value"] == 1000
    assert r["unrealized"] == 400


def test_unrealized_empty_store(tmp_path):
    path = _fresh(tmp_path)
    assert engine.unrealized(path) == {"cost_basis": 0.0, "value": 0.0,
                                       "unrealized": 0.0}
