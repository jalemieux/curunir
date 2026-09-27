"""The optional ``agent`` field through WS, Portal and Local Web (phase 2)."""
import asyncio
import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.channels._agent_field import agent_of, call_provider, request_cancel
from src.channels.base import OutgoingMessage
from src.channels.local_web import LocalWebChannel
from src.channels.portal import PortalChannel
from src.channels.ws import WebSocketChannel
from src.config import AgentConfig

AGENTS = [
    {"name": "everyday", "persona": "default", "description": "g", "default": True},
    {"name": "finance", "persona": "finance", "description": "m", "default": False},
]


# --- helpers ----------------------------------------------------------------

def test_agent_of_normalizes():
    assert agent_of({"agent": " finance "}) == "finance"
    assert agent_of({"agent": ""}) is None
    assert agent_of({"agent": 3}) is None
    assert agent_of({}) is None
    assert agent_of(None) is None


def test_call_provider_only_forwards_agent_when_set():
    one_arg = lambda sid: ["one", sid]  # a legacy provider (no agent kwarg)
    assert call_provider(one_arg, "s") == ["one", "s"]
    seen = {}

    def two(sid, agent=None):
        seen["agent"] = agent
        return [sid]

    assert call_provider(two, "s", agent="finance") == ["s"] and seen["agent"] == "finance"


def test_request_cancel_forwards_agent_when_set():
    calls = []
    cb = lambda sid, agent=None: (calls.append((sid, agent)) or True)
    assert request_cancel(cb, "s") and request_cancel(cb, "s", "finance")
    assert calls == [("s", None), ("s", "finance")]
    assert request_cancel(None, "s") is False


# --- WebSocket --------------------------------------------------------------

@pytest.mark.asyncio
async def test_ws_process_inbound_carries_agent(tmp_path):
    q: asyncio.Queue = asyncio.Queue()
    ch = WebSocketChannel(q, uploads_dir=str(tmp_path), agents=AGENTS)
    await ch._process_inbound({"content": "hi", "agent": "finance"}, "sid-1")
    await ch._process_inbound({"content": "hi"}, "sid-2")
    assert q.get_nowait().agent == "finance"
    assert q.get_nowait().agent is None


@pytest.mark.asyncio
async def test_ws_hello_advertises_agents_and_send_echoes_agent(tmp_path):
    q: asyncio.Queue = asyncio.Queue()
    ch = WebSocketChannel(q, model="m", agents=AGENTS)
    sent = []

    class FakeWS:
        async def send(self, raw):
            sent.append(json.loads(raw))

    ws = FakeWS()
    await ch._send_hello(ws, "sid")
    assert sent[-1]["agents"] == AGENTS
    ch._connections["sid"] = ws
    await ch.send(OutgoingMessage(content="x", channel="cli", session_id="sid", reply_address={}, agent="finance"))
    assert sent[-1]["agent"] == "finance"
    await ch.send(OutgoingMessage(content="x", channel="cli", session_id="sid", reply_address={}))
    assert "agent" not in sent[-1]
    # a single-agent hello has no agents key
    solo = WebSocketChannel(q)
    await solo._send_hello(ws, "sid")
    assert "agents" not in sent[-1]


# --- Portal -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_portal_user_message_and_requests_carry_agent(tmp_path):
    q: asyncio.Queue = asyncio.Queue()
    seen = {}

    def history(sid, agent=None):
        seen["history"] = (sid, agent)
        return []

    cancels = []
    ch = PortalChannel(
        in_queue=q, url="ws://x", token="t", uploads_dir=str(tmp_path),
        history_provider=history,
        cancel_session=lambda sid, agent=None: (cancels.append((sid, agent)) or True),
    )
    sent = []

    class FakeConn:
        async def send(self, raw):
            sent.append(json.loads(raw))

    ch._connection = FakeConn()
    await ch._handle_user_message({"content": "hi", "session_id": "s1", "agent": "finance"})
    assert q.get_nowait().agent == "finance"
    await ch._handle_user_message({"command": "slash", "text": "/help", "session_id": "s1", "agent": "finance"})
    assert q.get_nowait().agent == "finance"
    await ch._handle_user_message({"command": "history_request", "session_id": "s1", "agent": "finance"})
    assert seen["history"] == ("s1", "finance") and sent[-1]["agent"] == "finance"
    await ch._handle_user_message({"command": "history_request", "session_id": "s1"})
    assert seen["history"] == ("s1", None) and "agent" not in sent[-1]
    await ch._handle_user_message({"command": "interrupt", "session_id": "s1", "agent": "finance"})
    assert cancels == [("s1", "finance")]
    await ch.send(OutgoingMessage(content="x", channel="portal", session_id="s1", reply_address={}, agent="finance"))
    assert sent[-1]["payload"]["agent"] == "finance"


# --- Local Web --------------------------------------------------------------

@pytest.fixture
def configs(tmp_path):
    root = tmp_path / "context"
    everyday = AgentConfig.for_agent("everyday", root, root, skill_dirs=[tmp_path / "s"])
    finance = AgentConfig.for_agent(
        "finance", root / "agents" / "finance", root, is_default=False,
        skill_allowlist=["balance-sheet"], skill_dirs=[tmp_path / "s"],
    )
    for c in (everyday, finance):
        (c.context_dir / "memory").mkdir(parents=True, exist_ok=True)
        (c.context_dir / "memory" / "notes.md").write_text(f"# {c.agent_name}")
    return {"everyday": everyday, "finance": finance}


@pytest.fixture
def local(configs):
    seen = {}

    def conversations(agent=None):
        seen["conversations"] = agent
        return [{"session_id": "c1", "title": agent or "default"}]

    ch = LocalWebChannel(
        in_queue=asyncio.Queue(), config=configs["everyday"], pairing_token=None,
        conversations_provider=conversations, agents=AGENTS, agent_configs=configs,
        cancel_session=MagicMock(return_value=True),
    )
    ch._seen = seen
    return ch


@pytest.mark.asyncio
async def test_local_web_inbound_frames_carry_agent_and_echo_it(local):
    frames = []

    async def respond(f):
        frames.append(f)

    await local._handle_inbound_frame({"content": "hi", "session_id": "u1", "agent": "finance"}, respond=respond)
    assert local.in_queue.get_nowait().agent == "finance"
    await local._handle_inbound_frame(
        {"command": "conversations_request", "session_id": "u1", "agent": "finance"}, respond=respond,
    )
    assert local._seen["conversations"] == "finance"
    assert frames[-1]["agent"] == "finance" and frames[-1]["conversations"][0]["title"] == "finance"
    await local._handle_inbound_frame({"command": "conversations_request", "session_id": "u1"}, respond=respond)
    assert local._seen["conversations"] is None and "agent" not in frames[-1]
    await local._handle_inbound_frame({"command": "interrupt", "session_id": "u1", "agent": "finance"})
    local.cancel_session.assert_called_with("u1", agent="finance")


def test_local_web_rest_follows_agent_and_gates_modules_per_agent(local):
    client = TestClient(local.app)
    # memory tree is per agent
    assert client.get("/api/memory").status_code == 200
    assert client.get("/api/memory/file", params={"path": "notes.md"}).json()["content"] == "# everyday"
    assert client.get("/api/memory/file", params={"agent": "finance", "path": "notes.md"}).json()["content"] == "# finance"
    # unknown agent → 404
    assert client.get("/api/memory", params={"agent": "ghost"}).status_code == 404
    # portfolio module: finance allowlists balance-sheet, the default persona does not
    assert client.get("/api/portfolio").status_code == 404
    assert client.get("/api/portfolio", params={"agent": "finance"}).status_code == 200
    # schedules are per agent stores
    r = client.post("/api/schedules", params={"agent": "finance"},
                    json={"id": "t1", "cron": "0 7 * * *", "prompt": "p"})
    assert r.status_code == 200, r.text
    assert [t["id"] for t in client.get("/api/schedules", params={"agent": "finance"}).json()] == ["t1"]
    assert client.get("/api/schedules").json() == []


@pytest.mark.asyncio
async def test_local_web_send_echoes_agent(local):
    sent = []

    class FakeWS:
        async def send_text(self, raw):
            sent.append(json.loads(raw))

    local._socket = FakeWS()
    await local.send(OutgoingMessage(content="x", channel="local_web", session_id="u1", reply_address={}, agent="finance"))
    assert sent[-1]["agent"] == "finance"


def test_local_web_agents_meta_includes_modules(local):
    meta = local._agents_meta()
    assert [(a["name"], a["modules"]) for a in meta] == [("everyday", []), ("finance", ["portfolio"])]
