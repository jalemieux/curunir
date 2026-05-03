"""Tests for PortalChannel.

We spin up a tiny in-process WebSocket server (via the `websockets`
library) that acts as a fake portal. PortalChannel connects to it,
exchanges messages, and we assert on what each side observed.
"""

import asyncio
import json
import logging

import pytest
import websockets

from src.channels import portal as portal_mod
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
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        history_provider=lambda: fake_history,
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({"type": "history_request"})
        raw = await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        msg = json.loads(raw)
        assert msg["type"] == "history_snapshot"
        assert msg["messages"] == fake_history
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


@pytest.fixture
async def reconnecting_portal_server():
    """Server that records every accepted connection on a queue.

    Unlike `portal_server`, does NOT cap at one connection — used to
    verify the agent reconnects after a silent / shutdown event.
    """
    accepts: asyncio.Queue = asyncio.Queue()

    async def handler(ws):
        await accepts.put(ws)
        try:
            async for _ in ws:
                pass
        except websockets.exceptions.ConnectionClosed:
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"ws://127.0.0.1:{port}/ws/agent"
    yield {"url": url, "accepts": accepts}
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_silent_server_triggers_reconnect(
    reconnecting_portal_server, monkeypatch
):
    """If no traffic arrives within _READ_TIMEOUT, agent reconnects."""
    monkeypatch.setattr(portal_mod, "_READ_TIMEOUT", 0.3)
    monkeypatch.setattr(portal_mod, "_BACKOFF_INITIAL", 0.05)

    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=reconnecting_portal_server["url"], token="t"
    )
    task = asyncio.create_task(ch.start())
    try:
        ws1 = await asyncio.wait_for(
            reconnecting_portal_server["accepts"].get(), timeout=2.0
        )
        # Server stays silent; agent should detect the read timeout, close,
        # and reconnect — yielding a second accept.
        ws2 = await asyncio.wait_for(
            reconnecting_portal_server["accepts"].get(), timeout=3.0
        )
        assert ws1 is not ws2
        assert ch._terminate is False
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_ping_does_not_log_unknown_type(portal_server, caplog):
    """Portal `{"type":"ping"}` is consumed silently by the agent."""
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t"
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        with caplog.at_level(logging.WARNING, logger="src.channels.portal"):
            await portal_server["send"]({"type": "ping"})
            await asyncio.sleep(0.1)
        assert in_q.empty()
        unknown = [r for r in caplog.records if "unknown type" in r.getMessage()]
        assert not unknown, f"unexpected warnings: {[r.getMessage() for r in unknown]}"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_shutdown_triggers_reconnect(
    reconnecting_portal_server, monkeypatch
):
    """Portal `{"type":"shutdown"}` causes the agent to close and reconnect."""
    monkeypatch.setattr(portal_mod, "_BACKOFF_INITIAL", 0.05)

    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=reconnecting_portal_server["url"], token="t"
    )
    task = asyncio.create_task(ch.start())
    try:
        ws1 = await asyncio.wait_for(
            reconnecting_portal_server["accepts"].get(), timeout=2.0
        )
        await ws1.send(json.dumps({"type": "shutdown"}))
        ws2 = await asyncio.wait_for(
            reconnecting_portal_server["accepts"].get(), timeout=3.0
        )
        assert ws1 is not ws2
        assert ch._terminate is False
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
