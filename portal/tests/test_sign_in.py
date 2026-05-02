import pytest

from portal import auth, db
from portal.sign_in import _rate_buckets


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    _rate_buckets.clear()
    yield
    _rate_buckets.clear()


@pytest.mark.asyncio
async def test_get_with_valid_token_renders_confirm_form(client):
    user = await db.create_user("ann@example.com")
    resp = await client.get(f"/sign-in?token={user.sign_in_token}")
    assert resp.status_code == 200
    assert "ann@example.com" in resp.text
    assert "<form" in resp.text and 'method="post"' in resp.text
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["referrer-policy"] == "no-referrer"


@pytest.mark.asyncio
async def test_get_with_invalid_token_renders_error(client):
    resp = await client.get("/sign-in?token=not-a-real-token")
    assert resp.status_code == 400
    assert "invalid" in resp.text.lower()


@pytest.mark.asyncio
async def test_get_with_deactivated_user_renders_error(client):
    user = await db.create_user("ben@example.com")
    await db.deactivate_user(user.id)
    resp = await client.get(f"/sign-in?token={user.sign_in_token}")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_sets_signed_cookie_and_redirects(client):
    user = await db.create_user("cara@example.com")
    resp = await client.post(
        "/sign-in", data={"token": user.sign_in_token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    set_cookie = resp.headers["set-cookie"]
    assert auth.SESSION_COOKIE in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=strict" in set_cookie.lower()
    assert "Path=/" in set_cookie
    # Test mode = debug=False default; Secure should be set
    assert "Secure" in set_cookie

    # Cookie should validate
    cookie_value = resp.cookies.get(auth.SESSION_COOKIE)
    assert auth.verify_session(cookie_value) == user.id


@pytest.mark.asyncio
async def test_post_with_invalid_token_returns_error(client):
    resp = await client.post(
        "/sign-in", data={"token": "garbage"}, follow_redirects=False
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_threshold(client, monkeypatch):
    from portal.config import settings
    monkeypatch.setattr(settings, "rate_limit_per_min", 3)
    for _ in range(3):
        await client.get("/sign-in?token=x")
    blocked = await client.get("/sign-in?token=x")
    assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_token_reusable_across_devices(client):
    user = await db.create_user("dan@example.com")
    r1 = await client.post(
        "/sign-in", data={"token": user.sign_in_token}, follow_redirects=False
    )
    r2 = await client.post(
        "/sign-in", data={"token": user.sign_in_token}, follow_redirects=False
    )
    assert r1.status_code == 302 and r2.status_code == 302


@pytest.mark.asyncio
async def test_needs_invite_page_renders(client):
    resp = await client.get("/needs-invite")
    assert resp.status_code == 200
    assert "invite" in resp.text.lower()
