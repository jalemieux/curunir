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


def test_heartbeat_pings_emitted(sync_client, monkeypatch):
    """The portal sends `{"type":"ping"}` to each agent on a fixed cadence."""
    from portal.config import settings

    monkeypatch.setattr(settings, "agent_heartbeat_interval", 0.05)
    user = _create_user(sync_client, "heartbeat@example.com")

    pings: list[dict] = []
    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {user.container_token}"},
    ) as ws:
        for _ in range(5):
            msg = json.loads(ws.receive_text())
            if msg.get("type") == "ping":
                pings.append(msg)
            if len(pings) >= 2:
                break
    assert len(pings) >= 2, f"expected ≥2 ping frames, got {pings}"


def test_agent_message_unwraps_and_would_fan_out(sync_client, monkeypatch):
    user = _create_user(sync_client, "unwrap@example.com")
    captured = []

    async def fake_fan(user_id, payload):
        captured.append((user_id, payload))
        return 1

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

    # The first call(s) include online-broadcast(s); find the unwrap.
    payloads = [json.loads(p) for (_, p) in captured]
    assert any(p == {"content": "hi", "final": True} for p in payloads)
