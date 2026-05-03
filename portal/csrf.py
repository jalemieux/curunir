"""Stateless CSRF tokens.

The token is HMAC(secret, f"{user_id}:csrf"). It binds to the signed-in
user (session cookie); a CSRF token issued for one user does not
validate POSTs as another user. Verified on state-changing POSTs in
addition to SameSite=Strict on the session cookie (defense-in-depth).
"""

import hashlib
import hmac

from portal.config import settings


def issue_csrf(user_id: int) -> str:
    msg = f"{user_id}:csrf".encode()
    return hmac.new(
        settings.portal_secret_key.encode(), msg, hashlib.sha256
    ).hexdigest()


def verify_csrf(user_id: int, token: str) -> bool:
    expected = issue_csrf(user_id)
    return hmac.compare_digest(expected, token or "")
