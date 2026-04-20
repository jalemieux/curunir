"""Tests for WebSocketChannel using real websockets connections (in-process)."""
import asyncio
import json

import pytest
import websockets
import websockets.exceptions

from src.channels.base import OutgoingMessage
from src.channels.ws import SESSION_ID, WebSocketChannel

# Use a fixed test port — pick something unlikely to clash
TEST_PORT = 18765
TEST_HOST = "127.0.0.1"


async def _start_channel(ch: WebSocketChannel) -> asyncio.Task:
    """Start the channel in a background task and wait for it to bind."""
    task = asyncio.create_task(ch.start())
    # Give the server a moment to bind
    await asyncio.sleep(0.05)
    return task


async def _stop_channel(task: asyncio.Task) -> None:
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_accepts_connection_and_forwards_to_queue():
    """Client message is forwarded to in_queue as IncomingMessage."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT)
    task = await _start_channel(ch)

    try:
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT}") as ws:
            await ws.send(json.dumps({"content": "hello", "command": None}))
            await asyncio.sleep(0.05)

        msg = q.get_nowait()
        assert msg.content == "hello"
        assert msg.channel == "cli"
        assert msg.session_id == SESSION_ID
        assert msg.reply_address == {}
        assert msg.command is None
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_send_delivers_to_connected_client():
    """WebSocketChannel.send() serialises and delivers to the connected client."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 1)
    task = await _start_channel(ch)

    try:
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 1}") as ws:
            outgoing = OutgoingMessage(
                content="Here is the answer",
                channel="cli",
                session_id=SESSION_ID,
                reply_address={},
                tool_calls=["Read foo.py"],
                final=True,
            )
            # Give _handle_connection a moment to set self._connection
            await asyncio.sleep(0.05)
            await ch.send(outgoing)

            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            data = json.loads(raw)
            assert data["content"] == "Here is the answer"
            assert data["tool_calls"] == ["Read foo.py"]
            assert data["final"] is True
            assert data["attachments"] is None
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_send_drops_when_no_client(caplog):
    """WebSocketChannel.send() logs a warning and drops msg when no client."""
    import logging
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 2)
    task = await _start_channel(ch)

    try:
        outgoing = OutgoingMessage(
            content="nobody home",
            channel="cli",
            session_id=SESSION_ID,
            reply_address={},
        )
        with caplog.at_level(logging.WARNING, logger="src.channels.ws"):
            await ch.send(outgoing)

        assert "No WebSocket client connected" in caplog.text
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_replaces_stale_connection_with_new_client():
    """A second connection replaces the first (last-connection-wins)."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 3)
    task = await _start_channel(ch)

    try:
        ws1 = await websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 3}")
        await asyncio.sleep(0.05)  # ensure first connection is registered

        # Second connection replaces the first
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 3}") as ws2:
            await asyncio.sleep(0.05)
            # ws2 should be the active connection — verify by sending a message
            await ws2.send(json.dumps({"content": "from-ws2", "command": None}))
            await asyncio.sleep(0.05)

        # ws1 should have been closed by the server
        with pytest.raises(websockets.exceptions.ConnectionClosedError):
            await ws1.recv()

        await asyncio.sleep(0.1)
        messages = []
        while not q.empty():
            messages.append(q.get_nowait())
        contents = [m.content for m in messages]
        assert "from-ws2" in contents
    finally:
        try:
            await ws1.close()
        except Exception:
            pass
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_client_reconnect_preserves_session():
    """After disconnect, a new connection can be made; session_id stays constant."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 4)
    task = await _start_channel(ch)

    try:
        # First connection
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 4}") as ws:
            await ws.send(json.dumps({"content": "first", "command": None}))
            await asyncio.sleep(0.05)
        # Disconnect triggers extract; wait for that to be queued
        await asyncio.sleep(0.1)

        # Second connection
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 4}") as ws:
            await ws.send(json.dumps({"content": "second", "command": None}))
            await asyncio.sleep(0.05)

        await asyncio.sleep(0.1)

        messages = []
        while not q.empty():
            messages.append(q.get_nowait())

        contents = [m.content for m in messages]
        commands = [m.command for m in messages]

        assert "first" in contents
        assert "second" in contents
        # After each disconnect an "extract" command is enqueued
        assert commands.count("extract") >= 2

        # All messages share the same session_id
        for m in messages:
            assert m.session_id == SESSION_ID
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_send_attachments_null_when_empty():
    """attachments field is null (not []) when the OutgoingMessage has no attachments."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 5)
    task = await _start_channel(ch)

    try:
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 5}") as ws:
            await asyncio.sleep(0.05)
            outgoing = OutgoingMessage(
                content="no attachments",
                channel="cli",
                session_id=SESSION_ID,
                reply_address={},
                attachments=[],   # explicitly empty list
            )
            await ch.send(outgoing)
            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            data = json.loads(raw)
            assert data["attachments"] is None
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_disconnect_enqueues_extract_command():
    """On client disconnect, an 'extract' command is enqueued to trigger memory extraction."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 6)
    task = await _start_channel(ch)

    try:
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 6}") as ws:
            pass  # immediately disconnect

        await asyncio.sleep(0.1)

        msg = q.get_nowait()
        assert msg.command == "extract"
        assert msg.session_id == SESSION_ID
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_send_includes_workflow_field():
    """workflow field from OutgoingMessage is included in the JSON payload."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 7)
    task = await _start_channel(ch)

    try:
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 7}") as ws:
            await asyncio.sleep(0.05)
            outgoing = OutgoingMessage(
                content="design phase",
                channel="cli",
                session_id=SESSION_ID,
                reply_address={},
                workflow={"steps": ["plan", "design", "implement"], "current": "design"},
            )
            await ch.send(outgoing)

            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            data = json.loads(raw)
            assert data["workflow"] == {"steps": ["plan", "design", "implement"], "current": "design"}
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_send_workflow_null_when_not_set():
    """workflow field is null when not set on OutgoingMessage."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 8)
    task = await _start_channel(ch)

    try:
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 8}") as ws:
            await asyncio.sleep(0.05)
            outgoing = OutgoingMessage(
                content="no workflow",
                channel="cli",
                session_id=SESSION_ID,
                reply_address={},
            )
            await ch.send(outgoing)

            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            data = json.loads(raw)
            assert data["workflow"] is None
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_send_includes_delta_field():
    """delta field from OutgoingMessage is included in the JSON payload."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 9)
    task = await _start_channel(ch)

    try:
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 9}") as ws:
            await asyncio.sleep(0.05)
            outgoing = OutgoingMessage(
                content="chunk",
                channel="cli",
                session_id=SESSION_ID,
                reply_address={},
                delta=True,
                final=False,
            )
            await ch.send(outgoing)

            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            data = json.loads(raw)
            assert data["delta"] is True
            assert data["content"] == "chunk"
            assert data["final"] is False
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_send_delta_defaults_false_in_payload():
    """delta key in JSON payload defaults to False when not set on OutgoingMessage."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 10)
    task = await _start_channel(ch)

    try:
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 10}") as ws:
            await asyncio.sleep(0.05)
            outgoing = OutgoingMessage(
                content="full",
                channel="cli",
                session_id=SESSION_ID,
                reply_address={},
            )
            await ch.send(outgoing)

            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            data = json.loads(raw)
            assert data["delta"] is False
    finally:
        await _stop_channel(task)
