"""Tests for the Local Web UI brokerage routes (under /api/portfolio/broker).

The routes are token-gated (401 before any other status), persona/module-gated
(404 when the portfolio module is off), and delegate to the reconcile
engine/adapter — thin, ValueError→400, no query logic. Adapters are injected as
test doubles, so no network anywhere.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from src.channels.local_web import LocalWebChannel
from src.config import AgentConfig
from src.portfolio import db as pdb
from src.portfolio import engine
from src.portfolio.brokers.base import (
    BrokerAccount,
    BrokerAuthRequired,
    BrokerPosition,
)

TOKEN = "test-token-123"


@pytest.fixture
def config(tmp_path):
    ctx = tmp_path / "context"
    (ctx / "memory").mkdir(parents=True)
    cfg = AgentConfig(
        identity_file=ctx / "identity.md",
        context_dir=ctx,
        usage_db=ctx / "usage.db",
        schedules_db=ctx / "schedules.db",
        portfolio_db=str(ctx / "memory" / "portfolio.db"),
        crm_db=str(ctx / "memory" / "crm.db"),
        skill_allowlist=["balance-sheet"],  # persona owns the portfolio module
    )
    pdb.init_db(cfg.portfolio_db)
    engine.add_asset(cfg.portfolio_db, {
        "class": "equity", "label": "AAPL lot", "ticker": "AAPL",
        "qty": 10, "value": 1200.0, "account": "A1"})
    return cfg


class _FakeAdapter:
    name = "etrade"

    def __init__(self, authed=True, raises=None):
        self._authed = authed
        self._raises = raises
        self.completed_with = None

    def auth_status(self):
        return {"name": "etrade", "authed": self._authed, "sandbox": True}

    def auth_start(self):
        return {"authorize_url": "https://us.etrade.com/authorize?token=REQ&key=ck"}

    def auth_complete(self, verifier):
        self.completed_with = verifier
        self._authed = True
        return self.auth_status()

    def fetch_accounts(self):
        if self._raises:
            raise self._raises
        return [BrokerAccount(account_id="A1")]

    def fetch_positions(self, account):
        if self._raises:
            raise self._raises
        return [BrokerPosition(account_id="A1", ticker="AAPL", qty=10,
                               price=150.0, market_value=1500.0, as_of="2026-07-15")]


def _channel(config, adapters):
    return LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN,
        broker_adapters_provider=lambda: adapters,
    )


def _client(config, adapters):
    return TestClient(_channel(config, adapters).app)


def _h():
    return {"X-Curunir-Token": TOKEN}


# --- auth gate --------------------------------------------------------------

@pytest.mark.parametrize("path,method", [
    ("/api/portfolio/broker/status", "get"),
    ("/api/portfolio/broker/accounts", "get"),
    ("/api/portfolio/broker/diff", "get"),
    ("/api/portfolio/broker/auth/start", "post"),
    ("/api/portfolio/broker/auth/verify", "post"),
    ("/api/portfolio/broker/sync", "post"),
])
def test_broker_routes_require_token(config, path, method):
    client = _client(config, [_FakeAdapter()])
    r = getattr(client, method)(path)  # no token
    assert r.status_code == 401


def test_broker_routes_404_when_module_disabled(config):
    config.skill_allowlist = ["crm"]  # no balance-sheet → portfolio module off
    client = _client(config, [_FakeAdapter()])
    # Token supplied, but module gating 404s (after the token check).
    assert client.get(
        "/api/portfolio/broker/status", headers=_h()).status_code == 404


def test_broker_status_token_precedence_over_404(config):
    config.skill_allowlist = ["crm"]  # module off
    client = _client(config, [_FakeAdapter()])
    # Unauthenticated probe gets 401, never 404 — can't enumerate modules.
    assert client.get("/api/portfolio/broker/status").status_code == 401


# --- reads ------------------------------------------------------------------

def test_broker_status_reports_adapters(config):
    client = _client(config, [_FakeAdapter()])
    body = client.get("/api/portfolio/broker/status", headers=_h()).json()
    assert body["available"] is True
    assert body["adapters"][0]["name"] == "etrade"


def test_broker_status_unconfigured_clean_payload(config):
    client = _client(config, [])
    body = client.get("/api/portfolio/broker/status", headers=_h()).json()
    assert body == {"available": False, "adapters": []}


def test_broker_diff_buckets(config):
    client = _client(config, [_FakeAdapter()])
    body = client.get("/api/portfolio/broker/diff", headers=_h()).json()
    entry = body["adapters"][0]
    assert [r["ticker"] for r in entry["diff"]["price_stale"]] == ["AAPL"]


def test_broker_diff_needs_reauth_reported(config):
    client = _client(config, [_FakeAdapter(raises=BrokerAuthRequired("reconnect"))])
    body = client.get("/api/portfolio/broker/diff", headers=_h()).json()
    assert body["adapters"][0]["needs_reauth"] is True


# --- writes -----------------------------------------------------------------

def test_broker_auth_start_returns_url(config):
    client = _client(config, [_FakeAdapter(authed=False)])
    r = client.post("/api/portfolio/broker/auth/start", headers=_h(), json={})
    assert r.status_code == 200
    assert "authorize" in r.json()["authorize_url"]


def test_broker_auth_verify_completes(config):
    adapter = _FakeAdapter(authed=False)
    client = _client(config, [adapter])
    r = client.post("/api/portfolio/broker/auth/verify", headers=_h(),
                    json={"verifier": "XYZ123"})
    assert r.status_code == 200
    assert adapter.completed_with == "XYZ123"
    assert r.json()["authed"] is True


def test_broker_auth_verify_missing_verifier_400(config):
    client = _client(config, [_FakeAdapter(authed=False)])
    r = client.post("/api/portfolio/broker/auth/verify", headers=_h(), json={})
    assert r.status_code == 400


def test_broker_auth_start_unknown_adapter_400(config):
    client = _client(config, [_FakeAdapter()])
    r = client.post("/api/portfolio/broker/auth/start", headers=_h(),
                    json={"adapter": "nope"})
    assert r.status_code == 400


def test_broker_sync_applies_and_reports(config):
    # Fresh store: the fake adapter reports AAPL @ qty 10 matching the seeded lot
    # (value stale 1200 → 1500), so sync re-prices it.
    client = _client(config, [_FakeAdapter()])
    r = client.post("/api/portfolio/broker/sync", headers=_h(), json={})
    assert r.status_code == 200
    entry = r.json()["adapters"][0]
    assert entry["report"]["applied"]["updated"]
    lot = [a for a in engine.list_assets(config.portfolio_db) if a["ticker"] == "AAPL"][0]
    assert lot["value"] == 1500.0


def test_broker_routes_handle_misconfigured_adapters_gracefully(config):
    # A bad BROKERAGE_ADAPTERS makes enabled_adapters raise ValueError; the
    # route must degrade to a friendly 400, not a 500 (service's never-raise
    # contract). Mirrors the portfolio tool's broad-except handling.
    def boom():
        raise ValueError("unknown brokerage adapter 'bogus'")

    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN,
        broker_adapters_provider=boom,
    )
    client = TestClient(ch.app, raise_server_exceptions=False)
    r = client.get("/api/portfolio/broker/status", headers=_h())
    assert r.status_code == 400
    assert "bogus" in r.json()["error"]
    # A write route degrades the same way.
    r2 = client.post("/api/portfolio/broker/sync", headers=_h(), json={})
    assert r2.status_code == 400
