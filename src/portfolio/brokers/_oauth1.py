"""Stdlib OAuth 1.0a HMAC-SHA1 request signer (RFC 5849).

Pure functions — no network, no state — so they're testable against the
canonical spec vector. Chosen over ``requests-oauthlib``/``rauth`` to keep the
zero-new-deps posture (consistent with the stdlib ``imaplib``/``smtplib``
email transport): ~60 lines of ``hmac``/``hashlib``/``urllib`` vs. a stale
third-party dep.

``sign`` computes the signature over an explicit param set; ``auth_header``
assembles a full ``Authorization: OAuth ...`` header (adding the oauth_*
protocol params and the computed signature).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse


def percent_encode(value: str) -> str:
    """RFC 3986 percent-encoding: only ``A-Za-z0-9-._~`` stay literal.

    ``urllib.parse.quote`` keeps ``_.-~`` unreserved already; passing
    ``safe=""`` also encodes ``/``, matching the OAuth spec exactly.
    """
    return urllib.parse.quote(str(value), safe="")


def _signature_base_string(method: str, url: str, params: dict) -> str:
    encoded = sorted(
        (percent_encode(k), percent_encode(v)) for k, v in params.items()
    )
    param_string = "&".join(f"{k}={v}" for k, v in encoded)
    return "&".join([
        method.upper(),
        percent_encode(url),
        percent_encode(param_string),
    ])


def sign(method: str, url: str, params: dict, *, consumer_secret: str,
         token_secret: str = "") -> str:
    """Compute the OAuth 1.0a HMAC-SHA1 signature (base64) for a request.

    ``params`` must include every request + oauth_* parameter *except*
    ``oauth_signature`` itself.
    """
    base_string = _signature_base_string(method, url, params)
    signing_key = f"{percent_encode(consumer_secret)}&{percent_encode(token_secret)}"
    digest = hmac.new(
        signing_key.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def auth_header(method: str, url: str, *, consumer_key: str, consumer_secret: str,
                token: str | None = None, token_secret: str = "",
                verifier: str | None = None, callback: str | None = None,
                params: dict | None = None, timestamp: str | None = None,
                nonce: str | None = None) -> str:
    """Build the ``Authorization: OAuth ...`` header value for a request.

    Combines the caller's request ``params`` (query/form args, which are folded
    into the signature) with the oauth_* protocol params, signs, and emits only
    the oauth_* params (quoted, percent-encoded) in the header — request params
    ride the URL/body separately.
    """
    oauth: dict[str, str] = {
        "oauth_consumer_key": consumer_key,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or "",
        "oauth_nonce": nonce or "",
        "oauth_version": "1.0",
    }
    if token:
        oauth["oauth_token"] = token
    if verifier:
        oauth["oauth_verifier"] = verifier
    if callback:
        oauth["oauth_callback"] = callback

    all_params = {**(params or {}), **oauth}
    oauth["oauth_signature"] = sign(
        method, url, all_params,
        consumer_secret=consumer_secret, token_secret=token_secret,
    )
    parts = ", ".join(
        f'{percent_encode(k)}="{percent_encode(v)}"'
        for k, v in sorted(oauth.items())
    )
    return f"OAuth {parts}"
