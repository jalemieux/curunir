"""Tests for the PORTAL_MODE=local profile.

Local mode seeds a single env-defined user at startup and drops the
magic-link sign-in / admin surface: `/` auto-issues the session cookie
and serves the chat UI directly. The container↔portal Bearer-token
path is unchanged — it authenticates against the seeded user's
container token.

These tests build a local-mode app via `create_app()` after
monkeypatching the settings singleton. The local-mode `client` fixture
shadows the hosted one from conftest, so the autouse `_clean_db`
fixture truncates against this app's pool.
"""

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from portal import auth, config, db
from portal.routing import routing


LOCAL_EMAIL = "local@curunir"
LOCAL_TOKEN = "local-mode-container-token"


def _build_local_app(monkeypatch):
    monkeypatch.setattr(config.settings, "portal_mode", "local")
    monkeypatch.setattr(config.settings, "local_user_email", LOCAL_EMAIL)
    monkeypatch.setattr(config.settings, "local_container_token", LOCAL_TOKEN)
    from portal.app import create_app
    return create_app()


@pytest.fixture
async def client(monkeypatch):
    """Local-mode async client. Shadows conftest's hosted `client`."""
    app = _build_local_app(monkeypatch)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture
def sync_client(monkeypatch):
    """Local-mode sync client for WebSocket tests (mirrors test_ws_agent)."""
    app = _build_local_app(monkeypatch)
    with TestClient(app) as c:
        async def _truncate():
            pool = db.get_pool()
            async with pool.acquire() as conn:
                await conn.execute("TRUNCATE users RESTART IDENTITY")

        # Don't truncate before yield — lifespan seeded the local user and
        # the WS auth test needs it. Clean up afterwards.
        yield c
        c.portal.call(_truncate)
    routing._routes.clear()


# ----- seeding -----


@pytest.mark.asyncio
async def test_lifespan_seeds_local_user(client):
    """The local-mode lifespan seeds the env-defined user into Postgres."""
    user = await db.get_active_user_by_container_token(LOCAL_TOKEN)
    assert user is not None
    assert user.email == LOCAL_EMAIL
    assert user.is_active is True


@pytest.mark.asyncio
async def test_ensure_local_user_is_idempotent(client):
    """Re-seeding keeps the same row and refreshes the container token."""
    first = await db.ensure_local_user()
    second = await db.ensure_local_user()
    assert first.id == second.id
    fetched = await db.get_active_user_by_container_token(LOCAL_TOKEN)
    assert fetched is not None and fetched.id == first.id


# ----- root: cookie + UI -----


@pytest.mark.asyncio
async def test_root_auto_issues_cookie_and_serves_ui(client):
    """First visit with no cookie: `/` mints the local session cookie and
    serves the chat UI directly (no redirect to /needs-invite)."""
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    assert b"<title>Curunir</title>" in resp.content
    assert b"/ws/browser" in resp.content

    set_cookie = resp.headers.get("set-cookie", "")
    assert auth.SESSION_COOKIE in set_cookie
    # Plain-HTTP local surface — the cookie must not be Secure-only.
    assert "secure" not in set_cookie.lower()


@pytest.mark.asyncio
async def test_root_serves_ui_when_already_authed(client):
    """A returning visit (valid cookie) still serves the UI in local mode."""
    user = await db.get_active_user_by_container_token(LOCAL_TOKEN)
    cookie = auth.sign_session(user.id)
    resp = await client.get(
        "/", cookies={auth.SESSION_COOKIE: cookie}, follow_redirects=False
    )
    assert resp.status_code == 200
    assert b"<title>Curunir</title>" in resp.content


@pytest.mark.asyncio
async def test_root_cookie_authenticates_a_real_session(client):
    """The cookie `/` issues verifies back to the seeded local user."""
    resp = await client.get("/")
    set_cookie = resp.headers["set-cookie"]
    value = set_cookie.split(f"{auth.SESSION_COOKIE}=", 1)[1].split(";", 1)[0]
    user_id = auth.verify_session(value)
    assert user_id is not None
    seeded = await db.get_active_user_by_container_token(LOCAL_TOKEN)
    assert user_id == seeded.id


# ----- auth surface omitted -----


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/sign-in", "/needs-invite", "/admin"])
async def test_sign_in_and_admin_routes_absent(client, path):
    """Local mode does not mount the sign-in / admin routers."""
    resp = await client.get(path, follow_redirects=False)
    assert resp.status_code == 404


# ----- container WebSocket -----


def test_ws_agent_authenticates_seeded_local_user(sync_client):
    """The container connects to /ws/agent with the seeded user's container
    token — the same Bearer-token path as hosted mode."""
    user = sync_client.portal.call(
        db.get_active_user_by_container_token, LOCAL_TOKEN
    )
    assert user is not None
    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {LOCAL_TOKEN}"},
    ) as ws:
        assert routing.agent_for(user.id) is not None
        ws.close()


def test_ws_agent_rejects_unknown_token_in_local_mode(sync_client):
    """A token that isn't the seeded user's is still rejected."""
    with pytest.raises(Exception):
        with sync_client.websocket_connect(
            "/ws/agent",
            headers={"Authorization": "Bearer not-the-local-token"},
        ):
            pass
