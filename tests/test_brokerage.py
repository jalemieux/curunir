"""Tests for the pluggable brokerage adapter framework.

Covers the pure OAuth 1.0a signer (RFC-vector), the normalized dataclasses,
the E*TRADE adapter (canned JSON → normalized positions, auth round-trip,
token persistence), and the config-driven registry. No network anywhere —
the HTTP layer is patched in every adapter test.
"""
from __future__ import annotations

import json
import os
import stat
from unittest.mock import patch

import pytest

from src.portfolio.brokers import _oauth1, base, registry


# --- OAuth 1.0a signer (pure, RFC/spec vector) ------------------------------

# Canonical HMAC-SHA1 vector from the OAuth 1.0a spec, Appendix A.5.1.
_VECTOR_PARAMS = {
    "oauth_consumer_key": "dpf43f3p2l4k3l03",
    "oauth_token": "nnch734d00sl2jdk",
    "oauth_signature_method": "HMAC-SHA1",
    "oauth_timestamp": "1191242096",
    "oauth_nonce": "kllo9940pd9333jh",
    "oauth_version": "1.0",
    "file": "vacation.jpg",
    "size": "original",
}


def test_oauth1_signature_matches_spec_vector():
    sig = _oauth1.sign(
        "GET",
        "http://photos.example.net/photos",
        _VECTOR_PARAMS,
        consumer_secret="kd94hf93k423kf44",
        token_secret="pfkkdhi9sl3r4s00",
    )
    assert sig == "tR3+Ty81lMeYAr/Fid0kMTYa/WM="


def test_oauth1_percent_encoding_reserved_chars():
    # RFC 3986: only A-Za-z0-9-._~ are unreserved; everything else is encoded.
    assert _oauth1.percent_encode("a b~c/d") == "a%20b~c%2Fd"
    assert _oauth1.percent_encode("") == ""


def test_oauth1_auth_header_includes_signature_and_oauth_params():
    header = _oauth1.auth_header(
        "GET",
        "http://photos.example.net/photos",
        consumer_key="dpf43f3p2l4k3l03",
        consumer_secret="kd94hf93k423kf44",
        token="nnch734d00sl2jdk",
        token_secret="pfkkdhi9sl3r4s00",
        params={"file": "vacation.jpg", "size": "original"},
        timestamp="1191242096",
        nonce="kllo9940pd9333jh",
    )
    assert header.startswith("OAuth ")
    assert 'oauth_consumer_key="dpf43f3p2l4k3l03"' in header
    assert 'oauth_signature="tR3%2BTy81lMeYAr%2FFid0kMTYa%2FWM%3D"' in header
    assert 'oauth_nonce="kllo9940pd9333jh"' in header


# --- normalized dataclasses -------------------------------------------------

def test_broker_account_and_position_dataclasses():
    acct = base.BrokerAccount(account_id="A1", name="Brokerage", type="MARGIN")
    assert acct.account_id == "A1" and acct.name == "Brokerage"

    pos = base.BrokerPosition(
        account_id="A1", ticker="AAPL", qty=10, price=150.0,
        market_value=1500.0, cost_basis=1200.0, as_of="2026-07-15",
    )
    assert pos.ticker == "AAPL" and pos.qty == 10
    assert pos.market_value == 1500.0 and pos.cost_basis == 1200.0


def test_broker_errors_are_exception_types():
    assert issubclass(base.BrokerAuthRequired, base.BrokerError)
    assert issubclass(base.BrokerError, Exception)


# --- registry (config-driven enablement) ------------------------------------

def test_registry_empty_env_yields_no_adapters():
    assert registry.enabled_adapters({"BROKERAGE_ADAPTERS": ""}) == []
    assert registry.enabled_adapters({}) == []


def test_registry_unknown_adapter_raises():
    with pytest.raises(ValueError):
        registry.enabled_adapters({"BROKERAGE_ADAPTERS": "bogus"})


def test_registry_etrade_without_keys_warns_and_skips(caplog):
    adapters = registry.enabled_adapters({"BROKERAGE_ADAPTERS": "etrade"})
    # Missing consumer key/secret → skipped, not crashed.
    assert adapters == []
    assert any("etrade" in r.message.lower() for r in caplog.records)


def test_registry_etrade_with_keys_builds_adapter(tmp_path):
    env = {
        "BROKERAGE_ADAPTERS": "etrade",
        "ETRADE_CONSUMER_KEY": "ck",
        "ETRADE_CONSUMER_SECRET": "cs",
        "ETRADE_TOKEN_FILE": str(tmp_path / ".etrade-tokens.json"),
    }
    adapters = registry.enabled_adapters(env)
    assert len(adapters) == 1
    assert adapters[0].name == "etrade"


# --- E*TRADE adapter --------------------------------------------------------

def _etrade(tmp_path, **over):
    from src.portfolio.brokers.etrade import ETradeAdapter
    return ETradeAdapter(
        consumer_key="ck",
        consumer_secret="cs",
        token_file=str(tmp_path / ".etrade-tokens.json"),
        sandbox=True,
        **over,
    )


def test_etrade_token_save_load_roundtrip_and_mode(tmp_path):
    a = _etrade(tmp_path)
    a._save_tokens({"oauth_token": "t", "oauth_token_secret": "s"})
    b = _etrade(tmp_path)
    toks = b._load_tokens()
    assert toks["oauth_token"] == "t" and toks["oauth_token_secret"] == "s"
    mode = stat.S_IMODE(os.stat(a.token_file).st_mode)
    assert mode == 0o600


def test_etrade_auth_status_unconfigured_when_no_tokens(tmp_path):
    a = _etrade(tmp_path)
    st = a.auth_status()
    assert st["authed"] is False


def test_etrade_auth_start_returns_authorize_url(tmp_path):
    a = _etrade(tmp_path)

    def fake_request(method, url, **kw):
        return "oauth_token=REQTOKEN&oauth_token_secret=REQSECRET"

    with patch.object(a, "_raw_request", side_effect=fake_request):
        out = a.auth_start()
    assert "REQTOKEN" in out["authorize_url"]
    assert "ck" in out["authorize_url"]  # consumer key in the authorize URL


def test_etrade_auth_complete_persists_access_tokens(tmp_path):
    a = _etrade(tmp_path)

    def fake_request(method, url, **kw):
        if "request_token" in url:
            return "oauth_token=REQTOKEN&oauth_token_secret=REQSECRET"
        if "access_token" in url:
            return "oauth_token=ACCESS&oauth_token_secret=ACCESSSECRET"
        return ""

    with patch.object(a, "_raw_request", side_effect=fake_request):
        a.auth_start()
        a.auth_complete("verifier123")
    toks = a._load_tokens()
    assert toks["oauth_token"] == "ACCESS"
    assert toks["oauth_token_secret"] == "ACCESSSECRET"
    assert a.auth_status()["authed"] is True


_ACCOUNTS_JSON = {
    "AccountListResponse": {
        "Accounts": {
            "Account": [
                {
                    "accountId": "83405188",
                    "accountIdKey": "dBZOKt9xDrtRSAOl4MSiiA",
                    "accountDesc": "Individual Brokerage",
                    "institutionType": "BROKERAGE",
                }
            ]
        }
    }
}

_PORTFOLIO_JSON = {
    "PortfolioResponse": {
        "AccountPortfolio": [
            {
                "Position": [
                    {
                        "symbolDescription": "AAPL",
                        "quantity": 10,
                        "pricePaid": 120.0,
                        "totalCost": 1200.0,
                        "marketValue": 1500.0,
                        "Product": {"symbol": "AAPL"},
                        "Quick": {"lastTrade": 150.0},
                    },
                    {
                        "symbolDescription": "MSFT",
                        "quantity": 5,
                        "pricePaid": 200.0,
                        "totalCost": 1000.0,
                        "marketValue": 1600.0,
                        "Product": {"symbol": "MSFT"},
                        "Quick": {"lastTrade": 320.0},
                    },
                ]
            }
        ]
    }
}


def _authed(tmp_path):
    a = _etrade(tmp_path)
    a._save_tokens({"oauth_token": "t", "oauth_token_secret": "s"})
    return a


def test_etrade_fetch_accounts_normalizes(tmp_path):
    a = _authed(tmp_path)
    with patch.object(a, "_get_json", return_value=_ACCOUNTS_JSON):
        accts = a.fetch_accounts()
    assert len(accts) == 1
    assert accts[0].account_id == "83405188"
    assert accts[0].raw["accountIdKey"] == "dBZOKt9xDrtRSAOl4MSiiA"


def test_etrade_fetch_positions_normalizes(tmp_path):
    a = _authed(tmp_path)
    acct = base.BrokerAccount(
        account_id="83405188", raw={"accountIdKey": "dBZOKt9xDrtRSAOl4MSiiA"}
    )
    with patch.object(a, "_get_json", return_value=_PORTFOLIO_JSON):
        positions = a.fetch_positions(acct)
    assert len(positions) == 2
    aapl = next(p for p in positions if p.ticker == "AAPL")
    assert aapl.qty == 10
    assert aapl.cost_basis == 1200.0
    assert aapl.price == 150.0
    assert aapl.market_value == 1500.0
    assert aapl.account_id == "83405188"


def test_etrade_hard_expired_token_raises_auth_required(tmp_path):
    a = _authed(tmp_path)

    def boom(*args, **kw):
        raise base.BrokerAuthRequired("token expired — re-auth in the web console")

    with patch.object(a, "_get_json", side_effect=boom):
        with pytest.raises(base.BrokerAuthRequired):
            a.fetch_accounts()


# --- service orchestration (shared by tool + UI) ----------------------------

class _FakeAdapter:
    """A test double implementing the BrokerageAdapter contract."""

    def __init__(self, name="etrade", authed=True, positions=None, accounts=None,
                 raises=None):
        self.name = name
        self._authed = authed
        self._positions = positions or []
        self._accounts = accounts or [base.BrokerAccount(account_id="A1")]
        self._raises = raises

    def auth_status(self):
        return {"name": self.name, "authed": self._authed, "sandbox": True}

    def auth_start(self):
        return {"authorize_url": "https://example/authorize?token=X"}

    def auth_complete(self, verifier):
        self._authed = True
        return self.auth_status()

    def fetch_accounts(self):
        if self._raises:
            raise self._raises
        return self._accounts

    def fetch_positions(self, account):
        if self._raises:
            raise self._raises
        return self._positions


def test_service_status_lists_adapters():
    from src.portfolio.brokers import service
    st = service.status([_FakeAdapter()])
    assert st["available"] is True
    assert st["adapters"][0]["name"] == "etrade"


def test_service_status_empty_when_no_adapters():
    from src.portfolio.brokers import service
    st = service.status([])
    assert st["available"] is False
    assert st["adapters"] == []


def test_service_diff_runs_reconcile(tmp_path):
    from src.portfolio import db as pdb, engine
    from src.portfolio.brokers import service
    path = str(tmp_path / "p.db")
    pdb.init_db(path)
    engine.add_asset(path, {"class": "equity", "label": "AAPL lot", "ticker": "AAPL",
                            "qty": 10, "value": 1200.0, "account": "A1"})
    pos = base.BrokerPosition(account_id="A1", ticker="AAPL", qty=10,
                              price=150.0, market_value=1500.0, as_of="2026-07-15")
    adapter = _FakeAdapter(positions=[pos])
    out = service.diff(path, [adapter])
    assert out["available"] is True
    entry = out["adapters"][0]
    assert entry["name"] == "etrade"
    assert [r["ticker"] for r in entry["diff"]["price_stale"]] == ["AAPL"]


def test_service_diff_needs_reauth_is_reported_not_raised(tmp_path):
    from src.portfolio import db as pdb
    from src.portfolio.brokers import service
    path = str(tmp_path / "p.db")
    pdb.init_db(path)
    adapter = _FakeAdapter(raises=base.BrokerAuthRequired("re-auth in web console"))
    out = service.diff(path, [adapter])
    entry = out["adapters"][0]
    assert entry.get("needs_reauth") is True
    assert "diff" not in entry


def test_service_sync_applies(tmp_path):
    from src.portfolio import db as pdb, engine
    from src.portfolio.brokers import service
    path = str(tmp_path / "p.db")
    pdb.init_db(path)
    pos = base.BrokerPosition(account_id="A1", ticker="TSLA", qty=4, price=250.0,
                              market_value=1000.0, cost_basis=900.0, as_of="2026-07-15")
    out = service.sync(path, [_FakeAdapter(positions=[pos])])
    entry = out["adapters"][0]
    assert [r["ticker"] for r in entry["report"]["applied"]["inserted"]] == ["TSLA"]
    assert [a["ticker"] for a in engine.list_assets(path)] == ["TSLA"]
