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
