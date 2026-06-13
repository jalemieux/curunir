"""Tests for the local web console channel (src/channels/local_web.py).

Covers the read-only REST endpoints, the loopback/token auth gate on
/ws/browser, the chat bridge into the agent queues, and send() delivery.
"""
import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.channels.base import IncomingMessage, OutgoingMessage
from src.channels.local_web import LocalWebChannel
from src.config import AgentConfig
from src.portfolio import db as pdb
from src.portfolio import engine
from src.usage_store import UsageRecord, UsageStore


GOOD_ORIGIN = {"Origin": "http://localhost:8766"}
TOKEN = "test-token-123"


@pytest.fixture
def config(tmp_path):
    ctx = tmp_path / "context"
    (ctx / "memory").mkdir(parents=True)
    cfg = AgentConfig(
        identity_file=ctx / "identity.md",
        context_dir=ctx,
        usage_db=ctx / "usage.db",
        portfolio_db=str(ctx / "memory" / "portfolio.db"),
    )
    # Seed a bit of each store so the REST endpoints have data to return.
    store = UsageStore(cfg.usage_db)
    store.record(UsageRecord(
        ts=datetime.now(timezone.utc), session_id="cli", model="m",
        prompt_tokens=10, completion_tokens=2, cost_usd=0.1, elapsed_sec=1.0,
    ))
    store.close()
    pdb.init_db(cfg.portfolio_db)
    engine.add_asset(cfg.portfolio_db, {"class": "cash", "label": "Bank", "value": 100})
    (ctx / "schedules.json").write_text(json.dumps([
        {"id": "t", "cron": "0 7 * * *", "prompt": "p", "enabled": True},
    ]))
    (ctx / "memory" / "profile.md").write_text("# Profile")
    return cfg


@pytest.fixture
def channel(config):
    return LocalWebChannel(
        in_queue=asyncio.Queue(),
        config=config,
        pairing_token=TOKEN,
        cancel_session=MagicMock(return_value=True),
    )


@pytest.fixture
def client(channel):
    return TestClient(channel.app)


# --- REST endpoints --------------------------------------------------------


def test_root_serves_spa(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_spa_assets_are_served(client):
    """Every local js/css asset the SPA references must be fetchable.

    Regression: the chat module was extracted into chat.js/chat.css/
    connection.js, but index.html referenced them with ``./`` paths that
    resolve to ``/chat.js`` while StaticFiles is mounted at ``/static`` —
    so every module 404'd and the chat pane rendered blank.
    """
    import re
    from urllib.parse import urljoin

    html = client.get("/").text
    refs = re.findall(r'["\']([^"\']+\.(?:js|css))["\']', html)
    local = [r for r in refs if not r.startswith("http")]
    assert local, "expected the SPA to reference local js/css assets"
    for ref in local:
        url = urljoin("/", ref)
        assert client.get(url).status_code == 200, f"{ref} -> {url} 404'd"


def test_api_usage(client):
    r = client.get("/api/usage", headers={"X-Curunir-Token": TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert body["rows"][0]["calls"] == 1


def test_api_portfolio(client):
    r = client.get("/api/portfolio", headers={"X-Curunir-Token": TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["networth"]["assets"] == 100


def test_api_schedules(client):
    r = client.get("/api/schedules", headers={"X-Curunir-Token": TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert body[0]["id"] == "t"
    assert body[0]["next_fire"]


def test_api_memory_tree(client):
    r = client.get("/api/memory", headers={"X-Curunir-Token": TOKEN})
    assert r.status_code == 200
    names = {n["name"] for n in r.json()}
    assert "profile.md" in names


def test_api_memory_file(client):
    r = client.get("/api/memory/file", params={"path": "profile.md"},
                   headers={"X-Curunir-Token": TOKEN})
    assert r.status_code == 200
    assert r.json()["content"] == "# Profile"


def test_api_memory_file_traversal_rejected(client):
    r = client.get("/api/memory/file", params={"path": "../identity.md"},
                   headers={"X-Curunir-Token": TOKEN})
    assert r.status_code == 400


def test_api_requires_token(client):
    assert client.get("/api/usage").status_code == 401
    assert client.get("/api/usage",
                      headers={"X-Curunir-Token": "wrong"}).status_code == 401


def test_api_token_via_query_param(client):
    r = client.get("/api/usage", params={"token": TOKEN})
    assert r.status_code == 200


# --- WebSocket auth gate ---------------------------------------------------


def test_ws_rejected_without_token(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/browser", headers=GOOD_ORIGIN):
            pass


def test_ws_rejected_with_bad_token(client):
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/ws/browser?token=wrong", headers=GOOD_ORIGIN
        ):
            pass


def test_ws_rejected_with_bad_origin(client):
    with pytest.raises(Exception):
        with client.websocket_connect(
            f"/ws/browser?token={TOKEN}",
            headers={"Origin": "https://evil.example.com"},
        ):
            pass


def test_ws_accepts_with_good_token(client):
    with client.websocket_connect(
        f"/ws/browser?token={TOKEN}", headers=GOOD_ORIGIN
    ) as ws:
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "agent_status"
        assert msg["status"] == "online"


def test_ws_no_token_configured_allows_connection(config):
    # pairing_token=None disables the gate (mirrors ws.py).
    ch = LocalWebChannel(in_queue=asyncio.Queue(), config=config, pairing_token=None)
    with TestClient(ch.app) as c:
        with c.websocket_connect("/ws/browser", headers=GOOD_ORIGIN) as ws:
            assert json.loads(ws.receive_text())["type"] == "agent_status"


# --- chat bridge -----------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_frame_enqueues_message(channel):
    await channel._handle_inbound_frame({"content": "hello world"})
    msg = channel.in_queue.get_nowait()
    assert isinstance(msg, IncomingMessage)
    assert msg.channel == "local_web"
    assert msg.session_id == "local"
    assert msg.content == "hello world"


@pytest.mark.asyncio
async def test_inbound_interrupt_calls_cancel(channel):
    await channel._handle_inbound_frame({"command": "interrupt"})
    channel.cancel_session.assert_called_once_with("local")
    assert channel.in_queue.empty()


@pytest.mark.asyncio
async def test_inbound_slash_forwarded(channel):
    await channel._handle_inbound_frame({"command": "slash", "text": "/skills"})
    msg = channel.in_queue.get_nowait()
    assert msg.command == "slash"
    assert msg.content == "/skills"


@pytest.mark.asyncio
async def test_inbound_history_request_responds_with_snapshot(config):
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN,
        history_provider=lambda sid: [{"role": "user", "content": "hi"}],
    )
    sent = []

    async def respond(frame):
        sent.append(frame)

    await ch._handle_inbound_frame({"command": "history_request"}, respond=respond)
    assert sent[0]["type"] == "history_snapshot"
    assert sent[0]["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_inbound_skills_request_responds_with_snapshot(config):
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN,
        skills_provider=lambda: [{"name": "demo"}],
    )
    sent = []

    async def respond(frame):
        sent.append(frame)

    await ch._handle_inbound_frame({"command": "skills_request"}, respond=respond)
    assert sent[0]["type"] == "skills_snapshot"
    assert sent[0]["skills"] == [{"name": "demo"}]


# --- send() ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_delivers_to_bound_socket(channel):
    sent = []

    class FakeSocket:
        async def send_text(self, data):
            sent.append(json.loads(data))

    channel._socket = FakeSocket()
    await channel.send(OutgoingMessage(
        content="reply", channel="local_web", session_id="local",
        reply_address={}, final=True,
    ))
    assert len(sent) == 1
    assert sent[0]["content"] == "reply"
    assert sent[0]["session_id"] == "local"
    assert sent[0]["final"] is True


@pytest.mark.asyncio
async def test_send_without_socket_is_noop(channel):
    # No socket bound — must not raise.
    await channel.send(OutgoingMessage(
        content="x", channel="local_web", session_id="local", reply_address={},
    ))
