"""E*TRADE brokerage adapter (OAuth 1.0a + accounts/portfolio endpoints).

Implements the three-step interactive auth (``auth_start`` → authorize URL,
``auth_complete`` → persist access tokens, ``auth_status``) plus
``fetch_accounts`` / ``fetch_positions`` returning normalized dataclasses.

The auth flow is one-time-per-day and interactive (E*TRADE has no refresh
token; access tokens die at midnight ET), so the operator completes the
verifier paste in the Local Web UI. Access tokens persist to
``context/.etrade-tokens.json`` (mode 0600, like ``.ws-token``). A hard-expired
token surfaces as :class:`BrokerAuthRequired` pointing at the web console.

Requests are signed by the stdlib :mod:`_oauth1` signer; the HTTP layer is a
thin ``urllib`` wrapper (``_raw_request`` / ``_get_json``) patched out in tests
— no live calls anywhere in the suite.
"""
from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from src.portfolio.brokers import _oauth1
from src.portfolio.brokers.base import (
    BrokerAccount,
    BrokerAuthRequired,
    BrokerError,
    BrokerPosition,
)

_PROD_BASE = "https://api.etrade.com"
_SANDBOX_BASE = "https://apisb.etrade.com"
# The authorize page is the same host in both tiers.
_AUTHORIZE_URL = "https://us.etrade.com/e/t/etws/authorize"


class ETradeAdapter:
    name = "etrade"

    def __init__(self, *, consumer_key: str, consumer_secret: str,
                 token_file: str, sandbox: bool = False):
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.token_file = token_file
        self.sandbox = sandbox
        self.base = _SANDBOX_BASE if sandbox else _PROD_BASE
        # Transient request-token pair, set by auth_start and consumed by
        # auth_complete. Also mirrored to disk so the flow survives a restart.
        self._request_token: str | None = None
        self._request_secret: str | None = None

    # --- token persistence -------------------------------------------------

    def _load_tokens(self) -> dict:
        try:
            with open(self.token_file, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, ValueError):
            return {}

    def _save_tokens(self, tokens: dict) -> None:
        directory = os.path.dirname(self.token_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        # Write then chmod 0600 (like the .ws-token pairing file).
        with open(self.token_file, "w", encoding="utf-8") as fh:
            json.dump(tokens, fh)
        os.chmod(self.token_file, 0o600)

    # --- auth (three-step interactive OAuth 1.0a) --------------------------

    def auth_status(self) -> dict:
        toks = self._load_tokens()
        authed = bool(toks.get("oauth_token") and toks.get("oauth_token_secret"))
        return {
            "name": self.name,
            "authed": authed,
            "sandbox": self.sandbox,
            "authed_at": toks.get("authed_at"),
        }

    def auth_start(self) -> dict:
        """Request a temporary token and return the E*TRADE authorize URL.

        The SPA opens the URL in a new tab; the user logs in and pastes the
        verifier code back into ``auth_complete``.
        """
        body = self._raw_request(
            "GET", f"{self.base}/oauth/request_token", callback="oob",
        )
        pairs = dict(urllib.parse.parse_qsl(body))
        req_token = pairs.get("oauth_token")
        req_secret = pairs.get("oauth_token_secret")
        if not req_token or not req_secret:
            raise BrokerError(f"E*TRADE request_token failed: {body!r}")
        self._request_token, self._request_secret = req_token, req_secret
        # Persist the pending pair so a restart mid-flow can still complete.
        toks = self._load_tokens()
        toks.update({"pending_token": req_token, "pending_secret": req_secret})
        self._save_tokens(toks)
        authorize_url = (
            f"{_AUTHORIZE_URL}?key={urllib.parse.quote(self.consumer_key)}"
            f"&token={urllib.parse.quote(req_token)}"
        )
        return {"authorize_url": authorize_url}

    def auth_complete(self, verifier: str) -> dict:
        """Exchange the pasted verifier for access tokens and persist them."""
        req_token = self._request_token
        req_secret = self._request_secret
        if not req_token or not req_secret:
            toks = self._load_tokens()
            req_token = toks.get("pending_token")
            req_secret = toks.get("pending_secret")
        if not req_token or not req_secret:
            raise BrokerError(
                "no pending request token — call auth_start (Connect) first"
            )
        body = self._raw_request(
            "GET", f"{self.base}/oauth/access_token",
            token=req_token, token_secret=req_secret,
            verifier=verifier.strip(),
        )
        pairs = dict(urllib.parse.parse_qsl(body))
        access = pairs.get("oauth_token")
        access_secret = pairs.get("oauth_token_secret")
        if not access or not access_secret:
            raise BrokerError(f"E*TRADE access_token failed: {body!r}")
        self._save_tokens({
            "oauth_token": access,
            "oauth_token_secret": access_secret,
            "authed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        self._request_token = self._request_secret = None
        return self.auth_status()

    # --- HTTP layer (patched in tests) -------------------------------------

    def _raw_request(self, method: str, url: str, *, token: str | None = None,
                     token_secret: str = "", verifier: str | None = None,
                     callback: str | None = None, params: dict | None = None) -> str:
        """Sign and issue one request; return the raw response text.

        Used for the auth-flow endpoints (form-encoded responses). The access
        token flow passes the request token; the fetch flow uses the stored
        access token via :meth:`_get_json`.
        """
        header = _oauth1.auth_header(
            method, url,
            consumer_key=self.consumer_key, consumer_secret=self.consumer_secret,
            token=token, token_secret=token_secret,
            verifier=verifier, callback=callback, params=params,
            timestamp=str(int(time.time())), nonce=secrets.token_hex(16),
        )
        full = url
        if params:
            full = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(full, method=method)
        req.add_header("Authorization", header)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:  # pragma: no cover - network path
            detail = e.read().decode("utf-8", "replace")
            if e.code in (401, 403):
                raise BrokerAuthRequired(
                    "E*TRADE rejected the token (expired or revoked) — re-auth "
                    "in the web console's Balance Sheet tab"
                ) from e
            raise BrokerError(f"E*TRADE HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:  # pragma: no cover - network path
            raise BrokerError(f"E*TRADE request failed: {e}") from e

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        """Authed GET returning parsed JSON. Raises BrokerAuthRequired if the
        stored access token is missing/expired."""
        toks = self._load_tokens()
        access = toks.get("oauth_token")
        access_secret = toks.get("oauth_token_secret")
        if not access or not access_secret:
            raise BrokerAuthRequired(
                "not connected to E*TRADE — connect in the web console's "
                "Balance Sheet tab"
            )
        url = f"{self.base}{path}"
        body = self._raw_request(
            "GET", url, token=access, token_secret=access_secret, params=params,
        )
        try:
            return json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:  # pragma: no cover
            raise BrokerError(f"E*TRADE returned non-JSON: {body[:200]!r}") from e

    # --- fetch (normalize vendor payloads) ---------------------------------

    def fetch_accounts(self) -> list[BrokerAccount]:
        data = self._get_json("/v1/accounts/list.json")
        raw = (
            data.get("AccountListResponse", {})
            .get("Accounts", {})
            .get("Account", [])
        )
        if isinstance(raw, dict):
            raw = [raw]
        out: list[BrokerAccount] = []
        for a in raw:
            out.append(BrokerAccount(
                account_id=str(a.get("accountId") or a.get("accountIdKey") or ""),
                name=a.get("accountDesc") or a.get("accountName"),
                type=a.get("accountType"),
                institution=a.get("institutionType"),
                raw=a,
            ))
        return out

    def fetch_positions(self, account: BrokerAccount) -> list[BrokerPosition]:
        key = account.raw.get("accountIdKey") or account.account_id
        data = self._get_json(f"/v1/accounts/{key}/portfolio.json")
        portfolios = (
            data.get("PortfolioResponse", {}).get("AccountPortfolio", [])
        )
        if isinstance(portfolios, dict):
            portfolios = [portfolios]
        asof = time.strftime("%Y-%m-%d")
        out: list[BrokerPosition] = []
        for pf in portfolios:
            rows = pf.get("Position", [])
            if isinstance(rows, dict):
                rows = [rows]
            for p in rows:
                product = p.get("Product", {})
                quick = p.get("Quick", {})
                ticker = (
                    product.get("symbol") or p.get("symbolDescription") or ""
                )
                qty = _num(p.get("quantity"))
                price = _num(quick.get("lastTrade")) or _num(p.get("pricePaid"))
                out.append(BrokerPosition(
                    account_id=account.account_id,
                    ticker=str(ticker),
                    qty=qty if qty is not None else 0.0,
                    price=price,
                    market_value=_num(p.get("marketValue")),
                    cost_basis=_num(p.get("totalCost")),
                    as_of=asof,
                    description=p.get("symbolDescription"),
                    raw=p,
                ))
        return out


def _num(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
