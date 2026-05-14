"""Tests for PortalChannel.

We spin up a tiny in-process WebSocket server (via the `websockets`
library) that acts as a fake portal. PortalChannel connects to it,
exchanges messages, and we assert on what each side observed.
"""

import asyncio
import json

import pytest
import websockets

from src.channels.base import OutgoingMessage
from src.channels.portal import PORTAL_SESSION_ID, PortalChannel


@pytest.fixture
async def portal_server():
    """Yield (url, recv_queue, send_callable, accept_callable, close_args).

    The server accepts ONE connection; the test drives it.
    """
    received: asyncio.Queue = asyncio.Queue()
    server_ws_holder: dict = {}
    accept_event = asyncio.Event()

    async def handler(ws):
        server_ws_holder["ws"] = ws
        accept_event.set()
        try:
            async for raw in ws:
                await received.put(raw)
        except websockets.exceptions.ConnectionClosed:
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"ws://127.0.0.1:{port}/ws/agent"

    async def send_to_channel(payload: dict):
        await accept_event.wait()
        await server_ws_holder["ws"].send(json.dumps(payload))

    async def close_with(code: int):
        await accept_event.wait()
        await server_ws_holder["ws"].close(code=code, reason="test")

    yield {
        "url": url,
        "received": received,
        "send": send_to_channel,
        "close": close_with,
        "accept": accept_event.wait,
    }
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_user_message_lands_on_in_queue(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="anything"
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "user_message",
            "payload": {"content": "hello"},
        })
        msg = await asyncio.wait_for(in_q.get(), timeout=2.0)
        assert msg.content == "hello"
        assert msg.channel == "portal"
        assert msg.session_id == PORTAL_SESSION_ID
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_outbound_message_wraps_with_type(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t"
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        # Wait for the client side to finish handshaking and assign _connection.
        for _ in range(200):
            if ch._connection is not None:
                break
            await asyncio.sleep(0.01)
        await ch.send(OutgoingMessage(
            content="hi", channel="portal",
            session_id=PORTAL_SESSION_ID, reply_address={}, final=True,
        ))
        raw = await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        msg = json.loads(raw)
        assert msg["type"] == "agent_message"
        assert msg["payload"]["content"] == "hi"
        assert msg["payload"]["final"] is True
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_history_request_invokes_provider_and_sends_snapshot(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    fake_history = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ]
    seen_sids: list[str] = []

    def provider(sid: str) -> list[dict]:
        seen_sids.append(sid)
        return fake_history

    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        history_provider=provider,
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({"type": "history_request"})
        raw = await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        msg = json.loads(raw)
        assert msg["type"] == "history_snapshot"
        assert msg["messages"] == fake_history
        # session_id rides on the envelope so the portal can route the
        # snapshot to the right browser.
        assert msg["session_id"] == PORTAL_SESSION_ID
        # No session_id in payload → falls back to the legacy id.
        assert seen_sids == [PORTAL_SESSION_ID]
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_user_message_uses_payload_session_id(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "user_message",
            "payload": {"content": "hi", "session_id": "abc"},
        })
        msg = await asyncio.wait_for(in_q.get(), timeout=2.0)
        assert msg.session_id == "abc"
        assert msg.content == "hi"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_user_message_without_session_id_uses_legacy_default(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "user_message",
            "payload": {"content": "hello"},
        })
        msg = await asyncio.wait_for(in_q.get(), timeout=2.0)
        assert msg.session_id == PORTAL_SESSION_ID
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_history_request_with_session_id_passed_to_provider(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    seen_sids: list[str] = []

    def provider(sid: str) -> list[dict]:
        seen_sids.append(sid)
        return []

    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        history_provider=provider,
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "history_request",
            "payload": {"session_id": "abc"},
        })
        # Wait for the snapshot reply (proxy for processing).
        await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        assert seen_sids == ["abc"]
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_outbound_payload_includes_session_id(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        for _ in range(200):
            if ch._connection is not None:
                break
            await asyncio.sleep(0.01)
        await ch.send(OutgoingMessage(
            content="hi", channel="portal",
            session_id="tab-42", reply_address={}, final=True,
        ))
        raw = await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        msg = json.loads(raw)
        assert msg["payload"]["session_id"] == "tab-42"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_interrupt_command_routes_to_cancel_session_callback(portal_server):
    """A {command: interrupt} payload from the portal triggers cancel_session
    and is NOT enqueued (the agent_worker is blocked while handle() runs).

    The interrupt cancels the session_id carried on the payload, not the
    legacy `"portal"` id — otherwise stop-button clicks from a tab using
    a non-default session id can't actually cancel that tab's loop
    (issue #88).
    """
    in_q: asyncio.Queue = asyncio.Queue()
    seen: list[str] = []
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        cancel_session=lambda sid: (seen.append(sid) or True),
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "user_message",
            "payload": {"command": "interrupt", "session_id": "tab-A"},
        })
        await asyncio.sleep(0.1)
        assert seen == ["tab-A"]
        assert in_q.empty()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_interrupt_without_session_id_falls_back_to_legacy(portal_server):
    """Stale browser builds that don't send session_id still get
    interrupts routed (to the legacy default)."""
    in_q: asyncio.Queue = asyncio.Queue()
    seen: list[str] = []
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        cancel_session=lambda sid: (seen.append(sid) or True),
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "user_message",
            "payload": {"command": "interrupt"},
        })
        await asyncio.sleep(0.1)
        assert seen == [PORTAL_SESSION_ID]
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_history_request_command_triggers_snapshot(portal_server):
    """Browser-driven history bootstrap: a user_message with
    command=history_request and a session_id should trigger a
    history_snapshot scoped to that session."""
    in_q: asyncio.Queue = asyncio.Queue()
    seen_sids: list[str] = []

    def provider(sid: str) -> list[dict]:
        seen_sids.append(sid)
        return [{"role": "user", "content": "u1"}]

    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        history_provider=provider,
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "user_message",
            "payload": {"command": "history_request", "session_id": "tab-Q"},
        })
        raw = await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        msg = json.loads(raw)
        assert msg["type"] == "history_snapshot"
        assert msg["session_id"] == "tab-Q"
        assert seen_sids == ["tab-Q"]
        assert in_q.empty()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_slash_command_dispatches_to_handler(portal_server, tmp_path):
    """/help via slash dispatch produces an agent_message back to the portal,
    not an in_queue entry."""
    in_q: asyncio.Queue = asyncio.Queue()
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    class _Agent:
        def request_cancel(self, sid):
            return False

    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        agent=_Agent(), skill_dirs=[skills_dir],
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "user_message",
            "payload": {"command": "slash", "text": "/help", "session_id": "tab-A"},
        })
        raw = await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        msg = json.loads(raw)
        assert msg["type"] == "agent_message"
        assert msg["payload"]["session_id"] == "tab-A"
        assert "Slash commands" in msg["payload"]["content"]
        assert in_q.empty()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_slash_clear_enqueues_clear_command(portal_server, tmp_path):
    in_q: asyncio.Queue = asyncio.Queue()
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    class _Agent:
        def request_cancel(self, sid):
            return False

    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        agent=_Agent(), skill_dirs=[skills_dir],
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "user_message",
            "payload": {"command": "slash", "text": "/clear", "session_id": "tab-A"},
        })
        msg = await asyncio.wait_for(in_q.get(), timeout=2.0)
        assert msg.command == "clear"
        assert msg.session_id == "tab-A"
        assert msg.channel == "portal"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_close_4003_terminal_does_not_reconnect(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t"
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["close"](4003)
        # start() should return cleanly without entering reconnect loop.
        await asyncio.wait_for(task, timeout=2.0)
        assert ch._terminate is True
    except asyncio.CancelledError:
        raise
    finally:
        if not task.done():
            task.cancel()


@pytest.mark.asyncio
async def test_close_4002_replaced_terminal(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t"
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["close"](4002)
        await asyncio.wait_for(task, timeout=2.0)
        assert ch._terminate is True
    finally:
        if not task.done():
            task.cancel()
