"""Round-trip tests with a real agent WS and a real browser WS.

Mock agent connects via /ws/agent. Browser connects via /ws/browser.
Browser sends a user message; agent should receive a wrapped envelope.
Agent sends an agent_message; browser should receive the unwrapped
payload. Browser connecting triggers a history_request to the agent.
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
            # Browser receives initial status + agent receives history_request.
            assert json.loads(browser_ws.receive_text()) == {
                "type": "agent_status", "status": "online",
            }
            hist = json.loads(agent_ws.receive_text())
            assert hist == {"type": "history_request"}

            # Browser sends a chat message.
            browser_ws.send_text(json.dumps({"content": "hello"}))
            wrapped = json.loads(agent_ws.receive_text())
            assert wrapped == {
                "type": "user_message",
                "payload": {"content": "hello"},
            }

            # Agent replies with an agent_message; browser sees unwrapped.
            agent_ws.send_text(json.dumps({
                "type": "agent_message",
                "payload": {"content": "hi back", "final": True},
            }))
            reply = json.loads(browser_ws.receive_text())
            assert reply == {"content": "hi back", "final": True}


def test_agent_history_snapshot_fans_out(sync_client):
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
            _ = agent_ws.receive_text()    # history_request

            agent_ws.send_text(json.dumps({
                "type": "history_snapshot",
                "messages": [
                    {"role": "user", "content": "one"},
                    {"role": "assistant", "content": "two"},
                ],
            }))
            snap = json.loads(browser_ws.receive_text())
            assert snap["type"] == "history_snapshot"
            assert len(snap["messages"]) == 2
