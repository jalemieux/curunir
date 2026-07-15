import json
from src.config import AgentConfig
from src.tools.portfolio_tool import exec_portfolio


def _cfg(tmp_path):
    cfg = AgentConfig()
    cfg.portfolio_db = str(tmp_path / "portfolio.db")
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


def test_tool_buy_sell_and_realized(tmp_path):
    cfg = _cfg(tmp_path)
    buy = json.loads(exec_portfolio({"action": "buy", "args": {
        "ticker": "SPCX", "qty": 100, "price": 150, "trade_date": "2024-01-10"}}, cfg))
    lot = buy["asset_id"]
    sell = json.loads(exec_portfolio({"action": "sell", "args": {
        "asset_id": lot, "qty": 40, "price": 170, "trade_date": "2026-06-13"}}, cfg))
    assert sell["realized_pnl"] == 800
    trades = json.loads(exec_portfolio({"action": "trades"}, cfg))
    assert len(trades) == 2
    realized = json.loads(exec_portfolio({"action": "realized"}, cfg))
    assert realized["total"] == 800


def test_tool_sell_unknown_lot_errors(tmp_path):
    cfg = _cfg(tmp_path)
    out = json.loads(exec_portfolio({"action": "sell", "args": {
        "asset_id": "nope", "qty": 1, "price": 1, "trade_date": "2026-01-01"}}, cfg))
    assert "error" in out


def test_engine_exception_hint_points_at_balance_sheet_skill(tmp_path):
    # An engine-level failure (bad args reaching the handler) must signpost the
    # owning skill so the model loads `balance-sheet`'s SKILL.md instead of
    # source-diving to reverse-engineer the syntax (#478).
    cfg = _cfg(tmp_path)
    out = json.loads(exec_portfolio({"action": "sell", "args": {
        "asset_id": "nope", "qty": 1, "price": 1, "trade_date": "2026-01-01"}}, cfg))
    assert "error" in out
    assert "balance-sheet" in out["hint"]


def test_tool_snapshot_list_show_diff(tmp_path):
    cfg = _cfg(tmp_path)
    exec_portfolio({"action": "add",
                    "args": {"class": "cash", "label": "Cash", "value": 1000}}, cfg)
    snap = json.loads(exec_portfolio({"action": "snapshot",
                                      "args": {"taken_at": "2026-01-01T09:00:00"}}, cfg))
    assert snap["net_worth"] == 1000
    exec_portfolio({"action": "set", "args": {"id": "cash", "fields": {"value": 1500}}}, cfg)
    json.loads(exec_portfolio({"action": "snapshot",
                               "args": {"taken_at": "2026-02-01T09:00:00"}}, cfg))
    snaps = json.loads(exec_portfolio({"action": "snapshots"}, cfg))
    assert len(snaps) == 2
    shown = json.loads(exec_portfolio({"action": "show_snapshot",
                                       "args": {"id": "latest"}}, cfg))
    assert shown["snapshot"]["net_worth"] == 1500
    diff = json.loads(exec_portfolio({"action": "diff_snapshots",
                                      "args": {"a": snap["id"], "b": "latest"}}, cfg))
    assert diff["net_worth"]["abs"] == 500


def test_tool_snapshot_dedup_warns(tmp_path):
    cfg = _cfg(tmp_path)
    exec_portfolio({"action": "add",
                    "args": {"class": "cash", "label": "Cash", "value": 1000}}, cfg)
    exec_portfolio({"action": "snapshot",
                    "args": {"taken_at": "2026-01-01T09:00:00"}}, cfg)
    dup = json.loads(exec_portfolio({"action": "snapshot",
                                     "args": {"taken_at": "2026-01-01T15:00:00"}}, cfg))
    assert "warning" in dup


# --- brokerage sync actions -------------------------------------------------

def test_tool_broker_unconfigured_is_friendly_not_raise(tmp_path):
    from unittest.mock import patch
    cfg = _cfg(tmp_path)
    with patch("src.tools.portfolio_tool.enabled_adapters", return_value=[]):
        out = json.loads(exec_portfolio({"action": "broker_diff"}, cfg))
    assert out["available"] is False
    assert "error" not in out  # clean payload, no raise


def test_tool_broker_diff_dispatches(tmp_path):
    from unittest.mock import patch
    from src.portfolio import db as pdb, engine
    from src.portfolio.brokers.base import BrokerAccount, BrokerPosition
    cfg = _cfg(tmp_path)
    pdb.init_db(cfg.portfolio_db)
    engine.add_asset(cfg.portfolio_db, {
        "class": "equity", "label": "AAPL lot", "ticker": "AAPL",
        "qty": 10, "value": 1200.0, "account": "A1"})

    class _Fake:
        name = "etrade"
        def auth_status(self): return {"name": "etrade", "authed": True}
        def fetch_accounts(self): return [BrokerAccount(account_id="A1")]
        def fetch_positions(self, acct):
            return [BrokerPosition(account_id="A1", ticker="AAPL", qty=10,
                                   price=150.0, market_value=1500.0, as_of="2026-07-15")]

    with patch("src.tools.portfolio_tool.enabled_adapters", return_value=[_Fake()]):
        out = json.loads(exec_portfolio({"action": "broker_diff"}, cfg))
    assert out["available"] is True
    assert [r["ticker"] for r in out["adapters"][0]["diff"]["price_stale"]] == ["AAPL"]


def test_tool_broker_sync_writes(tmp_path):
    from unittest.mock import patch
    from src.portfolio import db as pdb, engine
    from src.portfolio.brokers.base import BrokerAccount, BrokerPosition
    cfg = _cfg(tmp_path)
    pdb.init_db(cfg.portfolio_db)

    class _Fake:
        name = "etrade"
        def auth_status(self): return {"name": "etrade", "authed": True}
        def fetch_accounts(self): return [BrokerAccount(account_id="A1")]
        def fetch_positions(self, acct):
            return [BrokerPosition(account_id="A1", ticker="TSLA", qty=4, price=250.0,
                                   market_value=1000.0, cost_basis=900.0, as_of="2026-07-15")]

    with patch("src.tools.portfolio_tool.enabled_adapters", return_value=[_Fake()]):
        out = json.loads(exec_portfolio({"action": "broker_sync"}, cfg))
    assert [r["ticker"] for r in out["adapters"][0]["report"]["applied"]["inserted"]] == ["TSLA"]
    assert [a["ticker"] for a in engine.list_assets(cfg.portfolio_db)] == ["TSLA"]


def test_tool_broker_accounts_dispatches(tmp_path):
    from unittest.mock import patch
    from src.portfolio.brokers.base import BrokerAccount
    cfg = _cfg(tmp_path)

    class _Fake:
        name = "etrade"
        def auth_status(self): return {"name": "etrade", "authed": True}
        def fetch_accounts(self): return [BrokerAccount(account_id="A1", name="Brokerage")]
        def fetch_positions(self, acct): return []

    with patch("src.tools.portfolio_tool.enabled_adapters", return_value=[_Fake()]):
        out = json.loads(exec_portfolio({"action": "broker_accounts"}, cfg))
    assert out["adapters"][0]["accounts"][0]["account_id"] == "A1"
