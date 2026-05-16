import json

import pytest
from fastapi.testclient import TestClient

from portal import db
from portal.app import app
from portal.routing import routing


@pytest.fixture
def sync_client():
    """Sync TestClient with its own lifespan-managed pool.

    TestClient runs lifespan/requests on its own portal loop. To avoid
    cross-loop pool conflicts with the async `client` fixture, this
    fixture is sync and uses TestClient.portal to run async DB setup
    on the SAME loop as the WS handler.
    """
    with TestClient(app) as c:
        # Truncate users on TestClient's loop so tests start clean
        # (the async _clean_db fixture only runs for async tests).
        async def _truncate():
            pool = db.get_pool()
            async with pool.acquire() as conn:
                await conn.execute("TRUNCATE users RESTART IDENTITY")

        c.portal.call(_truncate)
        yield c
        c.portal.call(_truncate)
    # Hard reset routing table after WS tests.
    routing._routes.clear()


def _create_user(client: TestClient, email: str):
    """Create a user on the TestClient's portal loop and return it."""
    return client.portal.call(db.create_user, email)


def test_ws_agent_rejects_missing_token(sync_client):
    with pytest.raises(Exception):
        with sync_client.websocket_connect("/ws/agent") as _:
            pass


def test_ws_agent_rejects_invalid_token(sync_client):
    with pytest.raises(Exception):
        with sync_client.websocket_connect(
            "/ws/agent", headers={"Authorization": "Bearer not-a-token"}
        ):
            pass


def test_ws_agent_accepts_valid_token_and_registers(sync_client):
    user = _create_user(sync_client, "agent-conn@example.com")
    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {user.container_token}"},
    ) as ws:
        # Connection registered; routing reports an agent for the user.
        assert routing.agent_for(user.id) is not None
        ws.close()


def test_ws_agent_second_connection_kicks_first(sync_client):
    user = _create_user(sync_client, "kick@example.com")
    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {user.container_token}"},
    ) as ws1:
        with sync_client.websocket_connect(
            "/ws/agent",
            headers={"Authorization": f"Bearer {user.container_token}"},
        ) as ws2:
            # ws1 should receive a close; ws2 should be the registered agent.
            assert routing.agent_for(user.id) is not None


def test_agent_message_routes_to_bound_session(sync_client, monkeypatch):
    user = _create_user(sync_client, "unwrap@example.com")
    captured = []

    async def fake_route(user_id, session_id, payload):
        captured.append((user_id, session_id, payload))
        return 1

    monkeypatch.setattr(routing, "route_to_session", fake_route)

    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {user.container_token}"},
    ) as ws:
        ws.send_text(json.dumps({
            "type": "agent_message",
            "payload": {"content": "hi", "final": True, "session_id": "tab-A"},
        }))
        ws.close()

    payloads = [(sid, json.loads(p)) for (_, sid, p) in captured]
    assert ("tab-A", {"content": "hi", "final": True, "session_id": "tab-A"}) in payloads


def test_agent_message_without_session_id_dropped(sync_client, monkeypatch):
    """No session_id → no routing target. Drop instead of fanning out
    to every tab, which was the cross-tab bleed we are fixing."""
    user = _create_user(sync_client, "drop@example.com")
    routed = []
    fanned = []

    async def fake_route(user_id, session_id, payload):
        routed.append((user_id, session_id, payload))
        return 0

    async def fake_fan(user_id, payload):
        fanned.append((user_id, payload))
        return 0

    monkeypatch.setattr(routing, "route_to_session", fake_route)
    monkeypatch.setattr(routing, "fan_out_to_browsers", fake_fan)

    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {user.container_token}"},
    ) as ws:
        ws.send_text(json.dumps({
            "type": "agent_message",
            "payload": {"content": "hi", "final": True},
        }))
        ws.close()

    assert routed == []
    # online/offline status broadcasts still go through fan_out — that's
    # expected. We just want to make sure the agent_message itself wasn't
    # broadcast.
    fanned_payloads = [json.loads(p) for (_, p) in fanned]
    assert all("content" not in p for p in fanned_payloads)


def test_history_snapshot_routes_by_session_id(sync_client, monkeypatch):
    user = _create_user(sync_client, "snap@example.com")
    captured = []

    async def fake_route(user_id, session_id, payload):
        captured.append((user_id, session_id, payload))
        return 1

    monkeypatch.setattr(routing, "route_to_session", fake_route)

    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {user.container_token}"},
    ) as ws:
        snapshot = {
            "type": "history_snapshot",
            "session_id": "tab-Z",
            "messages": [{"role": "user", "content": "u1"}],
        }
        ws.send_text(json.dumps(snapshot))
        ws.close()

    targets = [(sid, json.loads(p)) for (_, sid, p) in captured]
    assert any(sid == "tab-Z" and p == snapshot for sid, p in targets)


def test_skills_snapshot_routes_by_session_id(sync_client, monkeypatch):
    user = _create_user(sync_client, "skillsnap@example.com")
    captured = []

    async def fake_route(user_id, session_id, payload):
        captured.append((user_id, session_id, payload))
        return 1

    monkeypatch.setattr(routing, "route_to_session", fake_route)

    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {user.container_token}"},
    ) as ws:
        snapshot = {
            "type": "skills_snapshot",
            "session_id": "tab-K",
            "skills": [{"name": "memo", "display_name": "Memo",
                        "summary": "A memo", "icon": "📊"}],
        }
        ws.send_text(json.dumps(snapshot))
        ws.close()

    targets = [(sid, json.loads(p)) for (_, sid, p) in captured]
    assert any(sid == "tab-K" and p == snapshot for sid, p in targets)
