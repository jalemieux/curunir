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
