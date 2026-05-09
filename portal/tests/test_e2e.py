"""Round-trip tests with a real agent WS and a real browser WS.

Mock agent connects via /ws/agent. Browser connects via /ws/browser.
Browser sends a user message tagged with a session_id; agent should
receive a wrapped envelope. Agent sends an agent_message tagged with
the same session_id; browser should receive the unwrapped payload.
History bootstrap is now browser-driven: the browser sends a
`history_request` user_message frame after connect, the agent responds
with a `history_snapshot` envelope tagged with the session_id, and the
portal routes that snapshot back to the originating tab only.
"""

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


def test_browser_to_agent_round_trip(sync_client):
    user = _create_user(sync_client, "e2e@example.com")
    cookie = auth.sign_session(user.id)

    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {user.container_token}"},
    ) as agent_ws:
        with sync_client.websocket_connect(
            "/ws/browser",
            cookies={auth.SESSION_COOKIE: cookie},
            headers=GOOD_ORIGIN,
        ) as browser_ws:
            # Browser receives initial status. History bootstrap is now
            # browser-driven, so no auto history_request frame fires.
            assert json.loads(browser_ws.receive_text()) == {
                "type": "agent_status", "status": "online",
            }

            # Browser sends a chat message tagged with its tab's session_id.
            # The portal binds this ws → "tab-A" so reply traffic for
            # session "tab-A" routes back here.
            browser_ws.send_text(json.dumps({
                "content": "hello",
                "session_id": "tab-A",
            }))
            wrapped = json.loads(agent_ws.receive_text())
            assert wrapped == {
                "type": "user_message",
                "payload": {"content": "hello", "session_id": "tab-A"},
            }

            # Agent replies with an agent_message tagged with the same
            # session_id; browser sees the unwrapped payload.
            agent_ws.send_text(json.dumps({
                "type": "agent_message",
                "payload": {
                    "content": "hi back", "final": True,
                    "session_id": "tab-A",
                },
            }))
            reply = json.loads(browser_ws.receive_text())
            assert reply == {
                "content": "hi back", "final": True, "session_id": "tab-A",
            }


def test_agent_history_snapshot_routes_to_session(sync_client):
    user = _create_user(sync_client, "snap@example.com")
    cookie = auth.sign_session(user.id)

    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {user.container_token}"},
    ) as agent_ws:
        with sync_client.websocket_connect(
            "/ws/browser",
            cookies={auth.SESSION_COOKIE: cookie},
            headers=GOOD_ORIGIN,
        ) as browser_ws:
            _ = browser_ws.receive_text()  # agent_status

            # Browser-driven history bootstrap: announce the tab's
            # session_id so the portal binds the ws, then ask the agent
            # for that session's history.
            browser_ws.send_text(json.dumps({
                "content": "",
                "command": "history_request",
                "session_id": "tab-S",
            }))
            req = json.loads(agent_ws.receive_text())
            assert req == {
                "type": "user_message",
                "payload": {
                    "content": "",
                    "command": "history_request",
                    "session_id": "tab-S",
                },
            }

            agent_ws.send_text(json.dumps({
                "type": "history_snapshot",
                "session_id": "tab-S",
                "messages": [
                    {"role": "user", "content": "one"},
                    {"role": "assistant", "content": "two"},
                ],
            }))
            snap = json.loads(browser_ws.receive_text())
            assert snap["type"] == "history_snapshot"
            assert snap["session_id"] == "tab-S"
            assert len(snap["messages"]) == 2


def test_cross_tab_traffic_does_not_bleed(sync_client):
    """The whole point of session-keyed routing: open two browser tabs,
    send agent_messages tagged with one tab's session_id, only that tab
    receives them. Regression guard for issue #88."""
    user = _create_user(sync_client, "bleed@example.com")
    cookie = auth.sign_session(user.id)

    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {user.container_token}"},
    ) as agent_ws:
        with sync_client.websocket_connect(
            "/ws/browser",
            cookies={auth.SESSION_COOKIE: cookie},
            headers=GOOD_ORIGIN,
        ) as tab_a, sync_client.websocket_connect(
            "/ws/browser",
            cookies={auth.SESSION_COOKIE: cookie},
            headers=GOOD_ORIGIN,
        ) as tab_b:
            # Drain the per-tab agent_status frames + bind each tab.
            _ = tab_a.receive_text()
            _ = tab_b.receive_text()
            tab_a.send_text(json.dumps({"content": "ping", "session_id": "A"}))
            _ = agent_ws.receive_text()  # the wrapped user_message
            tab_b.send_text(json.dumps({"content": "ping", "session_id": "B"}))
            _ = agent_ws.receive_text()

            # Agent emits a frame for session A. Only tab_a should see it.
            agent_ws.send_text(json.dumps({
                "type": "agent_message",
                "payload": {
                    "content": "for-A", "final": True, "session_id": "A",
                },
            }))
            received_a = json.loads(tab_a.receive_text())
            assert received_a["content"] == "for-A"

            # Now emit a frame for session B; it must reach tab_b only.
            agent_ws.send_text(json.dumps({
                "type": "agent_message",
                "payload": {
                    "content": "for-B", "final": True, "session_id": "B",
                },
            }))
            received_b = json.loads(tab_b.receive_text())
            assert received_b["content"] == "for-B"
