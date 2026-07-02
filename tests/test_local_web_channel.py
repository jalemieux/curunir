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
from src.crm import db as crm_db
from src.crm import engine as crm_engine
from src.portfolio import db as pdb
from src.portfolio import engine
from src.schedule_store import db as sdb
from src.schedule_store import engine as sengine
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
        schedules_db=ctx / "schedules.db",
        portfolio_db=str(ctx / "memory" / "portfolio.db"),
        crm_db=str(ctx / "memory" / "crm.db"),
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
    crm_db.init_db(cfg.crm_db)
    crm_engine.add_lead(cfg.crm_db, {"name": "Jane", "email": "jane@x.com",
                                     "source": "beta-signup"})
    sdb.init_db(str(cfg.schedules_db))
    sengine.create(
        str(cfg.schedules_db),
        {"id": "t", "cron": "0 7 * * *", "prompt": "p", "enabled": True},
    )
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


def _portfolio_client(config):
    """A client whose persona owns the portfolio module (balance-sheet)."""
    config.skill_allowlist = ["balance-sheet"]
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN
    )
    return TestClient(ch.app)


def test_api_portfolio(config):
    client = _portfolio_client(config)
    r = client.get("/api/portfolio", headers={"X-Curunir-Token": TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["networth"]["assets"] == 100


def test_api_portfolio_404_when_module_disabled(config):
    # Marketing-style allowlist: no balance-sheet → module off → 404.
    config.skill_allowlist = ["crm", "research"]
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN
    )
    client = TestClient(ch.app)
    assert client.get(
        "/api/portfolio", headers={"X-Curunir-Token": TOKEN}
    ).status_code == 404


def test_api_portfolio_404_for_default_persona(config):
    # default persona: None allowlist → no modules → 404.
    config.skill_allowlist = None
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN
    )
    client = TestClient(ch.app)
    assert client.get(
        "/api/portfolio", headers={"X-Curunir-Token": TOKEN}
    ).status_code == 404


def test_api_portfolio_token_takes_precedence_over_404(config):
    # Unauthenticated probe gets 401, not 404 — can't enumerate modules.
    config.skill_allowlist = ["crm"]  # module disabled
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN
    )
    client = TestClient(ch.app)
    assert client.get("/api/portfolio").status_code == 401


def _crm_client(config):
    """A client whose persona owns the crm module (crm skill)."""
    config.skill_allowlist = ["crm"]
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN
    )
    return TestClient(ch.app)


def test_api_crm(config):
    client = _crm_client(config)
    r = client.get("/api/crm", headers={"X-Curunir-Token": TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["pipeline"]["total"] == 1
    assert body["leads"][0]["name"] == "Jane"


def test_api_crm_404_when_module_disabled(config):
    # Finance-style allowlist: no crm → module off → 404.
    config.skill_allowlist = ["balance-sheet", "research"]
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN
    )
    client = TestClient(ch.app)
    assert client.get(
        "/api/crm", headers={"X-Curunir-Token": TOKEN}
    ).status_code == 404


def test_api_crm_404_for_default_persona(config):
    # default persona: None allowlist → no modules → 404.
    config.skill_allowlist = None
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN
    )
    client = TestClient(ch.app)
    assert client.get(
        "/api/crm", headers={"X-Curunir-Token": TOKEN}
    ).status_code == 404


def test_api_crm_token_takes_precedence_over_404(config):
    # Unauthenticated probe gets 401, not 404 — can't enumerate modules.
    config.skill_allowlist = ["balance-sheet"]  # module disabled
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN
    )
    client = TestClient(ch.app)
    assert client.get("/api/crm").status_code == 401


def test_api_crm_requires_token(config):
    client = _crm_client(config)
    assert client.get("/api/crm").status_code == 401


def test_api_schedules(client):
    r = client.get("/api/schedules", headers={"X-Curunir-Token": TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert body[0]["id"] == "t"
    assert body[0]["next_fire"]


# --- schedule mutations ----------------------------------------------------


def _schedule_ids(client):
    r = client.get("/api/schedules", headers={"X-Curunir-Token": TOKEN})
    return [s["id"] for s in r.json()]


def test_create_schedule(client):
    r = client.post(
        "/api/schedules",
        headers={"X-Curunir-Token": TOKEN},
        json={"id": "new", "cron": "0 9 * * *", "prompt": "do it"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "new"
    assert body["cron"] == "0 9 * * *"
    assert body["enabled"] is True
    assert "new" in _schedule_ids(client)


def test_create_schedule_bad_cron_400(client):
    r = client.post(
        "/api/schedules",
        headers={"X-Curunir-Token": TOKEN},
        json={"id": "bad", "cron": "not a cron", "prompt": "p"},
    )
    assert r.status_code == 400
    assert "error" in r.json()


def test_create_schedule_duplicate_400(client):
    r = client.post(
        "/api/schedules",
        headers={"X-Curunir-Token": TOKEN},
        json={"id": "t", "cron": "0 9 * * *", "prompt": "p"},
    )
    assert r.status_code == 400
    assert "error" in r.json()


def test_create_schedule_missing_field_400(client):
    r = client.post(
        "/api/schedules",
        headers={"X-Curunir-Token": TOKEN},
        json={"id": "x", "cron": "0 9 * * *"},
    )
    assert r.status_code == 400


def test_create_schedule_skill_not_allowed_400(config):
    config.skill_allowlist = ["allowed-skill"]
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN
    )
    with TestClient(ch.app) as c:
        r = c.post(
            "/api/schedules",
            headers={"X-Curunir-Token": TOKEN},
            json={"id": "x", "cron": "0 9 * * *", "prompt": "p",
                  "skill": "forbidden"},
        )
        assert r.status_code == 400
        assert "error" in r.json()


def test_update_schedule(client):
    r = client.put(
        "/api/schedules/t",
        headers={"X-Curunir-Token": TOKEN},
        json={"cron": "30 8 * * *", "prompt": "changed"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["cron"] == "30 8 * * *"
    assert body["prompt"] == "changed"


def test_update_schedule_bad_cron_400(client):
    r = client.put(
        "/api/schedules/t",
        headers={"X-Curunir-Token": TOKEN},
        json={"cron": "nope"},
    )
    assert r.status_code == 400


def test_update_schedule_not_found_400(client):
    r = client.put(
        "/api/schedules/missing",
        headers={"X-Curunir-Token": TOKEN},
        json={"prompt": "x"},
    )
    assert r.status_code == 400


def test_toggle_schedule_flips_enabled(client):
    r = client.post("/api/schedules/t/toggle", headers={"X-Curunir-Token": TOKEN})
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    r2 = client.post("/api/schedules/t/toggle", headers={"X-Curunir-Token": TOKEN})
    assert r2.json()["enabled"] is True


def test_toggle_schedule_not_found_400(client):
    r = client.post(
        "/api/schedules/missing/toggle", headers={"X-Curunir-Token": TOKEN}
    )
    assert r.status_code == 400


def test_delete_schedule(client):
    r = client.delete("/api/schedules/t", headers={"X-Curunir-Token": TOKEN})
    assert r.status_code == 200
    assert "t" not in _schedule_ids(client)


def test_delete_schedule_not_found_400(client):
    r = client.delete("/api/schedules/missing", headers={"X-Curunir-Token": TOKEN})
    assert r.status_code == 400


def test_schedule_mutations_require_token(client):
    # No token / wrong token rejected on every mutating route.
    assert client.post(
        "/api/schedules", json={"id": "z", "cron": "0 9 * * *", "prompt": "p"}
    ).status_code == 401
    assert client.put(
        "/api/schedules/t", json={"prompt": "x"},
        headers={"X-Curunir-Token": "wrong"},
    ).status_code == 401
    assert client.post("/api/schedules/t/toggle").status_code == 401
    assert client.delete(
        "/api/schedules/t", headers={"X-Curunir-Token": "wrong"}
    ).status_code == 401
    # Nothing mutated.
    assert _schedule_ids(client) == ["t"]


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


def test_ws_meta_frame_carries_model_and_persona(config):
    """After 'online', the console receives a meta frame for the header."""
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN,
        model="anthropic/x", persona="finance",
    )
    with TestClient(ch.app) as c:
        with c.websocket_connect(
            f"/ws/browser?token={TOKEN}", headers=GOOD_ORIGIN
        ) as ws:
            assert json.loads(ws.receive_text())["type"] == "agent_status"
            meta = json.loads(ws.receive_text())
            assert meta["type"] == "meta"
            assert meta["model"] == "anthropic/x"
            assert meta["persona"] == "finance"


def _meta_frame(ch):
    with TestClient(ch.app) as c:
        with c.websocket_connect(
            f"/ws/browser?token={TOKEN}", headers=GOOD_ORIGIN
        ) as ws:
            json.loads(ws.receive_text())  # agent_status
            return json.loads(ws.receive_text())


def test_ws_meta_frame_lists_enabled_modules_for_finance(config):
    config.skill_allowlist = ["balance-sheet", "research"]
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN,
        persona="finance",
    )
    assert _meta_frame(ch)["modules"] == ["portfolio"]


def test_ws_meta_frame_lists_crm_module_for_marketing(config):
    config.skill_allowlist = ["crm", "research"]  # no balance-sheet
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN,
        persona="marketing",
    )
    assert _meta_frame(ch)["modules"] == ["crm"]


def test_ws_meta_frame_finance_excludes_crm(config):
    config.skill_allowlist = ["balance-sheet", "research"]  # no crm
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN,
        persona="finance",
    )
    assert "crm" not in _meta_frame(ch)["modules"]


def test_ws_meta_frame_no_modules_for_default(config):
    config.skill_allowlist = None  # default persona — full catalog, no allowlist
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN,
    )
    assert _meta_frame(ch)["modules"] == []


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


# --- conversation sidebar (multi-conversation) -----------------------------


@pytest.mark.asyncio
async def test_inbound_conversations_request_responds_with_snapshot(config):
    convs = [
        {"session_id": "cli", "channel": "ws", "title": "First"},
        {"session_id": "portal", "channel": "portal", "title": "Second"},
    ]
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN,
        conversations_provider=lambda: convs,
    )
    sent = []

    async def respond(frame):
        sent.append(frame)

    await ch._handle_inbound_frame(
        {"command": "conversations_request"}, respond=respond
    )
    assert sent[0]["type"] == "conversations_snapshot"
    assert sent[0]["conversations"] == convs


@pytest.mark.asyncio
async def test_inbound_frame_uses_explicit_session_id(channel):
    await channel._handle_inbound_frame(
        {"content": "hi", "session_id": "abc-123"}
    )
    msg = channel.in_queue.get_nowait()
    assert msg.session_id == "abc-123"
    assert msg.content == "hi"


@pytest.mark.asyncio
async def test_inbound_frame_defaults_to_local_session_id(channel):
    await channel._handle_inbound_frame({"content": "hi"})
    msg = channel.in_queue.get_nowait()
    assert msg.session_id == "local"


@pytest.mark.asyncio
async def test_inbound_clear_with_session_id_enqueues_clear(channel):
    await channel._handle_inbound_frame(
        {"command": "clear", "session_id": "abc-123", "content": ""}
    )
    msg = channel.in_queue.get_nowait()
    assert msg.command == "clear"
    assert msg.session_id == "abc-123"


@pytest.mark.asyncio
async def test_inbound_interrupt_cancels_explicit_session(channel):
    await channel._handle_inbound_frame(
        {"command": "interrupt", "session_id": "abc-123"}
    )
    channel.cancel_session.assert_called_once_with("abc-123")
    assert channel.in_queue.empty()


@pytest.mark.asyncio
async def test_history_snapshot_echoes_requested_session_id(config):
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN,
        history_provider=lambda sid: [{"role": "user", "content": sid}],
    )
    sent = []

    async def respond(frame):
        sent.append(frame)

    await ch._handle_inbound_frame(
        {"command": "history_request", "session_id": "abc-123"}, respond=respond
    )
    assert sent[0]["session_id"] == "abc-123"
    assert sent[0]["messages"] == [{"role": "user", "content": "abc-123"}]


@pytest.mark.asyncio
async def test_skills_snapshot_echoes_requested_session_id(config):
    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=config, pairing_token=TOKEN,
        skills_provider=lambda: [{"name": "demo"}],
    )
    sent = []

    async def respond(frame):
        sent.append(frame)

    await ch._handle_inbound_frame(
        {"command": "skills_request", "session_id": "abc-123"}, respond=respond
    )
    assert sent[0]["session_id"] == "abc-123"


# --- client_msg_id idempotency (durable-frame replay dedup) ----------------


@pytest.mark.asyncio
async def test_duplicate_client_msg_id_enqueues_once(channel):
    # The browser buffers durable frames and replays them on reconnect, so a
    # frame can arrive twice. A repeated client_msg_id must enqueue only once.
    frame = {"content": "hi", "client_msg_id": "abc"}
    await channel._handle_inbound_frame(frame)
    await channel._handle_inbound_frame(frame)
    assert channel.in_queue.qsize() == 1


@pytest.mark.asyncio
async def test_distinct_client_msg_ids_enqueue_twice(channel):
    await channel._handle_inbound_frame({"content": "a", "client_msg_id": "id1"})
    await channel._handle_inbound_frame({"content": "b", "client_msg_id": "id2"})
    assert channel.in_queue.qsize() == 2


@pytest.mark.asyncio
async def test_missing_client_msg_id_always_enqueues(channel):
    # Back-compat: a client that doesn't stamp ids is never deduped.
    await channel._handle_inbound_frame({"content": "a"})
    await channel._handle_inbound_frame({"content": "a"})
    assert channel.in_queue.qsize() == 2


@pytest.mark.asyncio
async def test_duplicate_slash_client_msg_id_enqueues_once(channel):
    frame = {"command": "slash", "text": "/skills", "client_msg_id": "s1"}
    await channel._handle_inbound_frame(frame)
    await channel._handle_inbound_frame(frame)
    assert channel.in_queue.qsize() == 1


@pytest.mark.asyncio
async def test_recent_msg_id_set_is_bounded(channel):
    # The dedup ledger is bounded; old ids age out so it can't grow unbounded.
    from src.channels.local_web import _RECENT_MSG_CAP

    for i in range(_RECENT_MSG_CAP + 50):
        await channel._handle_inbound_frame(
            {"content": "x", "client_msg_id": f"id{i}"}
        )
    assert len(channel._recent_msg_ids) <= _RECENT_MSG_CAP


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
