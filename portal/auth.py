from typing import Optional

from fastapi import Cookie, HTTPException, Response, status
from itsdangerous import BadSignature, Signer

from portal import db
from portal.config import settings
from portal.db import User


SESSION_COOKIE = "portal_session"
COOKIE_VERSION = 1


def _signer() -> Signer:
    return Signer(settings.portal_secret_key, salt="portal-session")


def sign_session(user_id: int) -> str:
    """Return cookie value: '<user_id>.<v>.<sig>'."""
    payload = f"{user_id}.{COOKIE_VERSION}"
    return _signer().sign(payload.encode()).decode()


def verify_session(cookie_value: str) -> Optional[int]:
    """Return user_id if cookie is valid signature, else None."""
    try:
        payload = _signer().unsign(cookie_value.encode()).decode()
    except BadSignature:
        return None
    parts = payload.split(".", 1)
    if len(parts) != 2:
        return None
    user_id_str, _v = parts
    try:
        return int(user_id_str)
    except ValueError:
        return None


def set_local_session_cookie(response: Response, user_id: int) -> None:
    """Attach the session cookie for the seeded local-mode user.

    Local mode has no per-request auth — it relies on localhost binding +
    the WebSocket Origin check — so the cookie is issued with
    ``secure=False`` (the local surface is plain HTTP, like sign-in under
    DEBUG). It still rides the same signed-cookie session the browser
    WebSocket authenticates against.
    """
    response.set_cookie(
        key=SESSION_COOKIE,
        value=sign_session(user_id),
        secure=False,
        httponly=True,
        samesite="strict",
        path="/",
    )


async def current_user(
    portal_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User:
    """FastAPI dependency. 401 if no/invalid cookie or user is inactive."""
    if not portal_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    user_id = verify_session(portal_session)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    user = await db.get_user_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    return user


async def optional_current_user(
    portal_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User | None:
    """Like current_user but returns None instead of raising 401."""
    if not portal_session:
        return None
    user_id = verify_session(portal_session)
    if user_id is None:
        return None
    user = await db.get_user_by_id(user_id)
    if user is None or not user.is_active:
        return None
    return user
