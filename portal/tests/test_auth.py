import pytest
from fastapi import Depends, FastAPI

from portal import auth, db
from portal.app import app


@pytest.mark.asyncio
async def test_sign_and_verify_roundtrip():
    cookie = auth.sign_session(42)
    assert auth.verify_session(cookie) == 42


@pytest.mark.asyncio
async def test_tampered_cookie_returns_none():
    cookie = auth.sign_session(42)
    tampered = cookie[:-1] + ("0" if cookie[-1] != "0" else "1")
    assert auth.verify_session(tampered) is None


@pytest.mark.asyncio
async def test_garbage_cookie_returns_none():
    assert auth.verify_session("not-a-real-cookie") is None
    assert auth.verify_session("") is None


@pytest.mark.asyncio
async def test_signed_payload_without_separator_returns_none():
    """A validly-signed payload missing the '.' separator must not crash."""
    signed = auth._signer().sign(b"abc").decode()
    assert auth.verify_session(signed) is None


@pytest.mark.asyncio
async def test_current_user_dependency_401_when_no_cookie(client):
    @app.get("/__test_protected")
    async def protected(user=Depends(auth.current_user)):
        return {"id": user.id}

    resp = await client.get("/__test_protected")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_current_user_dependency_returns_user(client):
    @app.get("/__test_who")
    async def who(user=Depends(auth.current_user)):
        return {"id": user.id, "email": user.email}

    user = await db.create_user("dee@example.com")
    cookie = auth.sign_session(user.id)
    resp = await client.get(
        "/__test_who", cookies={auth.SESSION_COOKIE: cookie}
    )
    assert resp.status_code == 200
    assert resp.json() == {"id": user.id, "email": "dee@example.com"}


@pytest.mark.asyncio
async def test_current_user_401_when_user_deactivated(client):
    @app.get("/__test_deactivated")
    async def deact(user=Depends(auth.current_user)):
        return {"id": user.id}

    user = await db.create_user("eve@example.com")
    cookie = auth.sign_session(user.id)
    await db.deactivate_user(user.id)

    resp = await client.get(
        "/__test_deactivated", cookies={auth.SESSION_COOKIE: cookie}
    )
    assert resp.status_code == 401
