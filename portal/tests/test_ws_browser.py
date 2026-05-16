import json

import pytest
from fastapi.testclient import TestClient

from portal import auth, db
from portal.app import app
from portal.routing import routing


GOOD_ORIGIN = {"Origin": "http://localhost:8000"}


@pytest.fixture
def sync_client():
    with TestClient(app) as c:
        async def _truncate():
            pool = db.get_pool()
            async with pool.acquire() as conn:
                await conn.execute("TRUNCATE users RESTART IDENTITY")
        c.portal.call(_truncate)
        yield c
        c.portal.call(_truncate)
    routing._routes.clear()


def _create_user(client: TestClient, email: str):
    return client.portal.call(db.create_user, email)


def test_browser_rejected_when_origin_missing(sync_client):
    user = _create_user(sync_client, "nb1@example.com")
    cookie = auth.sign_session(user.id)
    with pytest.raises(Exception):
        with sync_client.websocket_connect(
            "/ws/browser", cookies={auth.SESSION_COOKIE: cookie}
        ):
            pass


def test_browser_rejected_when_origin_mismatch(sync_client):
    user = _create_user(sync_client, "nb2@example.com")
    cookie = auth.sign_session(user.id)
    with pytest.raises(Exception):
        with sync_client.websocket_connect(
            "/ws/browser",
            cookies={auth.SESSION_COOKIE: cookie},
            headers={"Origin": "https://evil.example.com"},
        ):
            pass


def test_browser_rejected_without_cookie(sync_client):
    with pytest.raises(Exception):
        with sync_client.websocket_connect("/ws/browser", headers=GOOD_ORIGIN):
            pass


def test_browser_rejected_when_user_inactive(sync_client):
    user = _create_user(sync_client, "inactive@example.com")
    cookie = auth.sign_session(user.id)
    sync_client.portal.call(db.deactivate_user, user.id)
    with pytest.raises(Exception):
        with sync_client.websocket_connect(
            "/ws/browser",
            cookies={auth.SESSION_COOKIE: cookie},
            headers=GOOD_ORIGIN,
        ):
            pass


def test_browser_accepts_and_pushes_offline_status(sync_client):
    user = _create_user(sync_client, "ok@example.com")
    cookie = auth.sign_session(user.id)
    with sync_client.websocket_connect(
        "/ws/browser",
        cookies={auth.SESSION_COOKIE: cookie},
        headers=GOOD_ORIGIN,
    ) as ws:
        msg = json.loads(ws.receive_text())
        assert msg == {"type": "agent_status", "status": "offline"}


def test_user_message_with_no_agent_replies_offline(sync_client):
    user = _create_user(sync_client, "offline@example.com")
    cookie = auth.sign_session(user.id)
    with sync_client.websocket_connect(
        "/ws/browser",
        cookies={auth.SESSION_COOKIE: cookie},
        headers=GOOD_ORIGIN,
    ) as ws:
        _status = ws.receive_text()
        ws.send_text(json.dumps({"content": "hi"}))
        reply = json.loads(ws.receive_text())
        assert reply["content"] == "Agent offline."
        assert reply["final"] is True


def test_history_request_with_no_agent_does_not_reply_offline(sync_client):
    """A control frame (history_request) that fails to forward must NOT
    produce an 'Agent offline.' chat bubble — otherwise it lingers in the
    transcript after the agent reconnects."""
    user = _create_user(sync_client, "ctrl@example.com")
    cookie = auth.sign_session(user.id)
    with sync_client.websocket_connect(
        "/ws/browser",
        cookies={auth.SESSION_COOKIE: cookie},
        headers=GOOD_ORIGIN,
    ) as ws:
        _status = ws.receive_text()
        ws.send_text(json.dumps({
            "content": "",
            "command": "history_request",
            "session_id": "tab-1",
        }))
        # Follow with a real message; its reply is the next frame, proving
        # no bubble was emitted for the history_request.
        ws.send_text(json.dumps({"content": "hi", "session_id": "tab-1"}))
        reply = json.loads(ws.receive_text())
        assert reply["content"] == "Agent offline."


def test_browser_frame_binds_session_on_routing_table(sync_client):
    """First inbound frame with session_id should bind the ws → session
    so that later agent traffic for that session lands on this socket."""
    user = _create_user(sync_client, "bind@example.com")
    cookie = auth.sign_session(user.id)
    with sync_client.websocket_connect(
        "/ws/browser",
        cookies={auth.SESSION_COOKIE: cookie},
        headers=GOOD_ORIGIN,
    ) as ws:
        _status = ws.receive_text()
        ws.send_text(json.dumps({
            "content": "hello",
            "session_id": "tab-XYZ",
        }))
        # Offline reply (no agent connected) — proves the frame was processed.
        _reply = ws.receive_text()
        assert routing.browsers_for_session(user.id, "tab-XYZ"), (
            "browser ws should be bound to session tab-XYZ"
        )
