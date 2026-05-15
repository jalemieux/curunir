"""Tests for WebSocketChannel using real websockets connections (in-process)."""
import asyncio
import json

import pytest
import websockets
import websockets.exceptions

from src.channels.base import OutgoingMessage
from src.channels.ws import WebSocketChannel

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


async def _read_hello(ws: websockets.ClientConnection) -> str:
    """Read the welcome frame and return the assigned session id."""
    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
    data = json.loads(raw)
    assert data.get("type") == "hello"
    sid = data.get("session_id")
    assert isinstance(sid, str) and sid
    return sid


@pytest.mark.asyncio
async def test_accepts_connection_and_forwards_to_queue():
    """Client message is forwarded to in_queue as IncomingMessage."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT)
    task = await _start_channel(ch)

    try:
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT}") as ws:
            sid = await _read_hello(ws)
            await ws.send(json.dumps({"content": "hello", "command": None}))
            await asyncio.sleep(0.05)

        # Drop the disconnect 'extract' message; we want the user message.
        msgs = []
        while not q.empty():
            msgs.append(q.get_nowait())
        user_msgs = [m for m in msgs if m.command != "extract"]
        assert len(user_msgs) == 1
        msg = user_msgs[0]
        assert msg.content == "hello"
        assert msg.channel == "cli"
        assert msg.session_id == sid
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
            sid = await _read_hello(ws)
            outgoing = OutgoingMessage(
                content="Here is the answer",
                channel="cli",
                session_id=sid,
                reply_address={},
                tool_calls=["Read foo.py"],
                final=True,
            )
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
            session_id="missing-session",
            reply_address={},
        )
        with caplog.at_level(logging.WARNING, logger="src.channels.ws"):
            await ch.send(outgoing)

        assert "No WebSocket client connected" in caplog.text
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_concurrent_connections_get_distinct_sessions():
    """Two simultaneous connections each get a distinct session id."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 3)
    task = await _start_channel(ch)

    try:
        ws1 = await websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 3}")
        ws2 = await websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 3}")
        sid1 = await _read_hello(ws1)
        sid2 = await _read_hello(ws2)
        assert sid1 != sid2

        # ws1 is still alive (not evicted by ws2's connect).
        await ws1.send(json.dumps({"content": "from-ws1", "command": None}))
        await ws2.send(json.dumps({"content": "from-ws2", "command": None}))
        await asyncio.sleep(0.05)

        # Each session sees only its own send() payloads.
        await ch.send(OutgoingMessage(
            content="for-1", channel="cli",
            session_id=sid1, reply_address={}, final=True,
        ))
        await ch.send(OutgoingMessage(
            content="for-2", channel="cli",
            session_id=sid2, reply_address={}, final=True,
        ))

        r1 = json.loads(await asyncio.wait_for(ws1.recv(), timeout=1.0))
        r2 = json.loads(await asyncio.wait_for(ws2.recv(), timeout=1.0))
        assert r1["content"] == "for-1"
        assert r2["content"] == "for-2"

        await ws1.close()
        await ws2.close()
        await asyncio.sleep(0.1)

        messages = []
        while not q.empty():
            messages.append(q.get_nowait())
        user_msgs = [m for m in messages if m.command != "extract"]
        # Each user message carries the session id of its originating connection.
        sessions_for_content = {m.content: m.session_id for m in user_msgs}
        assert sessions_for_content == {"from-ws1": sid1, "from-ws2": sid2}
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_disconnect_emits_extract_only_for_disconnected_session():
    """Disconnecting one client emits 'extract' for that session only."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 4)
    task = await _start_channel(ch)

    try:
        ws1 = await websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 4}")
        ws2 = await websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 4}")
        sid1 = await _read_hello(ws1)
        sid2 = await _read_hello(ws2)

        await ws1.close()
        await asyncio.sleep(0.1)

        # ws2's connection is still in the registry.
        assert sid2 in ch._connections
        assert sid1 not in ch._connections

        # Drain queue; only sid1 should have an extract.
        extracts = []
        while not q.empty():
            m = q.get_nowait()
            if m.command == "extract":
                extracts.append(m.session_id)
        assert extracts == [sid1]

        await ws2.close()
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_hello_with_prior_session_id_resumes_and_evicts_stale():
    """A hello frame with a known session id evicts the prior socket for that id."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 11)
    task = await _start_channel(ch)

    try:
        ws_a = await websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 11}")
        ws_other = await websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 11}")
        sid_a = await _read_hello(ws_a)
        sid_other = await _read_hello(ws_other)

        # New client wants to resume sid_a.
        ws_b = await websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 11}")
        await _read_hello(ws_b)  # initial minted id (will be re-keyed)
        await ws_b.send(json.dumps({"type": "hello", "session_id": sid_a}))
        # Server replies with another hello frame echoing the new id.
        raw = await asyncio.wait_for(ws_b.recv(), timeout=1.0)
        echoed = json.loads(raw)
        assert echoed["type"] == "hello"
        assert echoed["session_id"] == sid_a

        # ws_a should have been closed; ws_other untouched.
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            await asyncio.wait_for(ws_a.recv(), timeout=1.0)

        # ws_other still connected: server send to sid_other reaches it.
        await ch.send(OutgoingMessage(
            content="still-here", channel="cli",
            session_id=sid_other, reply_address={}, final=True,
        ))
        msg = json.loads(await asyncio.wait_for(ws_other.recv(), timeout=1.0))
        assert msg["content"] == "still-here"

        await ws_b.close()
        await ws_other.close()
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_send_to_unknown_session_warns_no_crash(caplog):
    """send() with a session id no socket holds is a no-op + warning."""
    import logging
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 12)
    task = await _start_channel(ch)
    try:
        with caplog.at_level(logging.WARNING, logger="src.channels.ws"):
            await ch.send(OutgoingMessage(
                content="lost", channel="cli",
                session_id="not-a-real-id", reply_address={}, final=True,
            ))
        assert "not-a-real-id" in caplog.text
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
            sid = await _read_hello(ws)
            outgoing = OutgoingMessage(
                content="no attachments",
                channel="cli",
                session_id=sid,
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
            sid = await _read_hello(ws)

        await asyncio.sleep(0.1)

        msg = q.get_nowait()
        assert msg.command == "extract"
        assert msg.session_id == sid
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
            sid = await _read_hello(ws)
            outgoing = OutgoingMessage(
                content="design phase",
                channel="cli",
                session_id=sid,
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
            sid = await _read_hello(ws)
            outgoing = OutgoingMessage(
                content="no workflow",
                channel="cli",
                session_id=sid,
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
            sid = await _read_hello(ws)
            outgoing = OutgoingMessage(
                content="chunk",
                channel="cli",
                session_id=sid,
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
            sid = await _read_hello(ws)
            outgoing = OutgoingMessage(
                content="full",
                channel="cli",
                session_id=sid,
                reply_address={},
            )
            await ch.send(outgoing)

            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            data = json.loads(raw)
            assert data["delta"] is False
    finally:
        await _stop_channel(task)


# ---------------------------------------------------------------------------
# Tests: _decode_attachments — inbound attachment validation
# ---------------------------------------------------------------------------
import base64

from src.channels.ws import _decode_attachments


class TestDecodeAttachments:
    def test_none_or_empty_returns_empty_list(self):
        assert _decode_attachments(None) == ([], None)
        assert _decode_attachments([]) == ([], None)

    def test_valid_image_and_text(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        items, err = _decode_attachments([
            {"filename": "a.png", "mime_type": "image/png",
             "data": base64.b64encode(png).decode()},
            {"filename": "notes.txt", "mime_type": "text/plain",
             "data": base64.b64encode(b"hello world").decode()},
        ])
        assert err is None
        assert len(items) == 2
        assert items[0]["bytes"] == png
        assert items[1]["bytes"] == b"hello world"

    def test_missing_field_rejected(self):
        items, err = _decode_attachments([
            {"filename": "a.png", "mime_type": "image/png"},  # no data
        ])
        assert items is None
        assert "data" in err

    def test_bad_base64_rejected(self):
        items, err = _decode_attachments([
            {"filename": "a.png", "mime_type": "image/png", "data": "!!!"},
        ])
        assert items is None
        assert "base64" in err.lower()

    def test_oversized_image_rejected(self):
        big = b"\x00" * (5 * 1024 * 1024 + 1)
        items, err = _decode_attachments([
            {"filename": "big.png", "mime_type": "image/png",
             "data": base64.b64encode(big).decode()},
        ])
        assert items is None
        assert "5" in err and "MB" in err

    def test_oversized_text_rejected(self):
        big = b"x" * (256 * 1024 + 1)
        items, err = _decode_attachments([
            {"filename": "big.txt", "mime_type": "text/plain",
             "data": base64.b64encode(big).decode()},
        ])
        assert items is None
        assert "256" in err and "KB" in err

    def test_total_batch_size_cap(self):
        # Six 4 MB images = 24 MB total, each under the 5 MB per-image cap.
        # Total cap (20 MB) should fire before the 6th decodes fully.
        four_mb = b"\x00" * (4 * 1024 * 1024)
        items, err = _decode_attachments([
            {"filename": f"a{i}.png", "mime_type": "image/png",
             "data": base64.b64encode(four_mb).decode()} for i in range(6)
        ])
        assert items is None
        assert "20" in err and "MB" in err

    def test_disallowed_image_mime_rejected(self):
        items, err = _decode_attachments([
            {"filename": "a.bmp", "mime_type": "image/bmp",
             "data": base64.b64encode(b"bmpdata").decode()},
        ])
        assert items is None
        assert "image/bmp" in err

    def test_docx_binary_accepted_under_doc_cap(self):
        """DOCX is binary (zip), so it must skip the UTF-8 check and use the 10 MB doc cap."""
        docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        payload = b"PK\x03\x04" + b"\xff\xfe" * 100  # zip magic + non-UTF-8 bytes
        items, err = _decode_attachments([
            {"filename": "memo.docx", "mime_type": docx_mime,
             "data": base64.b64encode(payload).decode()},
        ])
        assert err is None
        assert len(items) == 1
        assert items[0]["mime_type"] == docx_mime
        assert items[0]["bytes"] == payload

    def test_oversized_docx_rejected(self):
        docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        big = b"\x00" * (10 * 1024 * 1024 + 1)
        items, err = _decode_attachments([
            {"filename": "big.docx", "mime_type": docx_mime,
             "data": base64.b64encode(big).decode()},
        ])
        assert items is None
        assert "10" in err and "MB" in err

    def test_non_allowlisted_mime_rejected(self):
        """Mimes outside the shared allowlist (image, PDF, DOCX, text/*, json) are refused."""
        items, err = _decode_attachments([
            {"filename": "blob.bin", "mime_type": "application/octet-stream",
             "data": base64.b64encode(b"\xff\xfe\xfa").decode()},
        ])
        assert items is None
        assert "unsupported mime type" in err
        assert "application/octet-stream" in err

    def test_text_mime_with_binary_payload_rejected_as_non_utf8(self):
        """A text/plain mime with binary bytes still fails the UTF-8 check."""
        items, err = _decode_attachments([
            {"filename": "fake.txt", "mime_type": "text/plain",
             "data": base64.b64encode(b"\xff\xfe\xfa").decode()},
        ])
        assert items is None
        assert "UTF-8" in err

    def test_attachments_must_be_list(self):
        items, err = _decode_attachments({"filename": "a.png"})
        assert items is None
        assert "list" in err.lower()


# ---------------------------------------------------------------------------
# Tests: _stage_attachments — staging decoded items to disk
# ---------------------------------------------------------------------------
import os as _os_stage

from src.channels.ws import _stage_attachments


class TestStageAttachments:
    def test_writes_files_under_session_uuid(self, tmp_uploads):
        items = [
            {"filename": "a.png", "mime_type": "image/png", "bytes": b"PNG"},
            {"filename": "notes.txt", "mime_type": "text/plain", "bytes": b"hi"},
        ]
        manifest = _stage_attachments(items, "sid1", str(tmp_uploads))
        assert len(manifest) == 2
        # Both files share one uuid subdirectory.
        uuids = {_os_stage.path.basename(_os_stage.path.dirname(m["path"])) for m in manifest}
        assert len(uuids) == 1
        # Shape matches email channel.
        assert set(manifest[0].keys()) == {"filename", "path", "mime_type", "size"}
        # Bytes landed on disk.
        for m, src in zip(manifest, items):
            with open(m["path"], "rb") as f:
                assert f.read() == src["bytes"]
            assert m["size"] == len(src["bytes"])

    def test_normalizes_unicode_whitespace_in_filename(self, tmp_uploads):
        # U+202F (narrow no-break space) in filename should become a regular space.
        items = [{"filename": "weird name.txt",
                  "mime_type": "text/plain", "bytes": b"x"}]
        manifest = _stage_attachments(items, "sid", str(tmp_uploads))
        assert manifest[0]["filename"] == "weird name.txt"
        assert _os_stage.path.isfile(manifest[0]["path"])

    def test_collision_suffix(self, tmp_uploads):
        items = [
            {"filename": "same.txt", "mime_type": "text/plain", "bytes": b"1"},
            {"filename": "same.txt", "mime_type": "text/plain", "bytes": b"2"},
            {"filename": "same.txt", "mime_type": "text/plain", "bytes": b"3"},
        ]
        manifest = _stage_attachments(items, "sid", str(tmp_uploads))
        names = [m["filename"] for m in manifest]
        assert names == ["same.txt", "same_1.txt", "same_2.txt"]
        for m, src in zip(manifest, items):
            with open(m["path"], "rb") as f:
                assert f.read() == src["bytes"]

    def test_empty_items_returns_empty_manifest(self, tmp_uploads):
        assert _stage_attachments([], "sid", str(tmp_uploads)) == []


# ---------------------------------------------------------------------------
# Tests: end-to-end inbound attachments through WebSocketChannel
# ---------------------------------------------------------------------------
import os as _os_e2e


class TestWsAttachmentsE2E:
    @pytest.mark.asyncio
    async def test_valid_batch_produces_incoming_message_with_manifest(self, tmp_uploads):
        q = asyncio.Queue()
        ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 20,
                              uploads_dir=str(tmp_uploads))
        task = await _start_channel(ch)
        try:
            async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 20}") as ws:
                await ws.send(json.dumps({
                    "content": "compare these",
                    "command": None,
                    "attachments": [
                        {"filename": "a.png", "mime_type": "image/png",
                         "data": base64.b64encode(b"PNGDATA").decode()},
                        {"filename": "b.txt", "mime_type": "text/plain",
                         "data": base64.b64encode(b"hello").decode()},
                    ],
                }))
                await asyncio.sleep(0.1)

            msg = q.get_nowait()
            assert msg.content == "compare these"
            assert msg.attachments is not None
            assert len(msg.attachments) == 2
            for m in msg.attachments:
                assert set(m.keys()) == {"filename", "path", "mime_type", "size"}
                assert _os_e2e.path.isfile(m["path"])
            parents = {_os_e2e.path.dirname(m["path"]) for m in msg.attachments}
            assert len(parents) == 1
        finally:
            await _stop_channel(task)

    @pytest.mark.asyncio
    async def test_invalid_payload_emits_error_and_drops_message(self, tmp_uploads):
        q = asyncio.Queue()
        ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 21,
                              uploads_dir=str(tmp_uploads))
        task = await _start_channel(ch)
        try:
            async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 21}") as ws:
                await _read_hello(ws)
                await ws.send(json.dumps({
                    "content": "oversized image",
                    "command": None,
                    "attachments": [{
                        "filename": "huge.png",
                        "mime_type": "image/png",
                        "data": base64.b64encode(b"\x00" * (5 * 1024 * 1024 + 1)).decode(),
                    }],
                }))
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                data = json.loads(raw)
                assert data["final"] is True
                assert "5 MB" in data["content"] or "exceeds" in data["content"]

            # Drain any messages; only 'extract' from disconnect should appear, no payload.
            messages = []
            await asyncio.sleep(0.1)
            while not q.empty():
                messages.append(q.get_nowait())
            # The bad-payload message must NOT have been enqueued.
            assert all(m.command == "extract" for m in messages)
        finally:
            await _stop_channel(task)

    @pytest.mark.asyncio
    async def test_no_attachments_key_still_queues_plain_message(self, tmp_uploads):
        q = asyncio.Queue()
        ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 22,
                              uploads_dir=str(tmp_uploads))
        task = await _start_channel(ch)
        try:
            async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 22}") as ws:
                await ws.send(json.dumps({"content": "no files", "command": None}))
                await asyncio.sleep(0.1)
            msg = q.get_nowait()
            assert msg.content == "no files"
            assert msg.attachments is None
        finally:
            await _stop_channel(task)


@pytest.mark.asyncio
async def test_interrupt_command_routes_to_cancel_session_callback():
    """Inbound {command: interrupt} bypasses the queue and calls cancel_session."""
    q = asyncio.Queue()
    seen: list[str] = []
    ch = WebSocketChannel(
        q, host=TEST_HOST, port=TEST_PORT + 23,
        cancel_session=lambda sid: (seen.append(sid) or True),
    )
    task = await _start_channel(ch)
    try:
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 23}") as ws:
            hello = json.loads(await ws.recv())
            sid = hello["session_id"]
            await ws.send(json.dumps({"command": "interrupt"}))
            await asyncio.sleep(0.1)
        assert seen == [sid]
        assert q.empty()
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_slash_frame_enqueued_as_command_slash():
    """Slash frames are forwarded onto the in_queue as IncomingMessage with
    command="slash" and the raw text as content. The channel does not
    interpret slash — agent_worker dispatches it."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 25)
    task = await _start_channel(ch)
    try:
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 25}") as ws:
            sid = await _read_hello(ws)
            await ws.send(json.dumps({"command": "slash", "text": "/help"}))
            await asyncio.sleep(0.1)
        msgs = []
        while not q.empty():
            msgs.append(q.get_nowait())
        slash_msgs = [m for m in msgs if m.command == "slash"]
        assert len(slash_msgs) == 1
        assert slash_msgs[0].content == "/help"
        assert slash_msgs[0].session_id == sid
        assert slash_msgs[0].channel == "cli"
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_slash_frame_preserves_arbitrary_text():
    """The channel forwards the raw slash text verbatim — including args and
    unknown command names — leaving interpretation to the dispatcher."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 26)
    task = await _start_channel(ch)
    try:
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 26}") as ws:
            await _read_hello(ws)
            await ws.send(json.dumps({"command": "slash", "text": "/foo bar baz"}))
            await asyncio.sleep(0.1)
        msgs = []
        while not q.empty():
            msgs.append(q.get_nowait())
        slash_msgs = [m for m in msgs if m.command == "slash"]
        assert len(slash_msgs) == 1
        assert slash_msgs[0].content == "/foo bar baz"
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_process_inbound_attachment_rejection_uses_local_session_id(tmp_uploads):
    """Regression for #98: _process_inbound's rejection path must use the
    per-call session_id, not a stray module-level SESSION_ID name."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 24,
                          uploads_dir=str(tmp_uploads))

    sent: list[OutgoingMessage] = []
    async def _capture(msg: OutgoingMessage) -> None:
        sent.append(msg)
    ch.send = _capture  # type: ignore[assignment]

    # Oversized attachment forces _decode_attachments to return an error,
    # exercising the SESSION_ID-bearing branch from the original bug.
    bad = {
        "content": "x",
        "command": None,
        "attachments": [{
            "filename": "huge.png",
            "mime_type": "image/png",
            "data": base64.b64encode(b"\x00" * (5 * 1024 * 1024 + 1)).decode(),
        }],
    }
    await ch._process_inbound(bad, "session-abc")

    assert q.empty()
    assert len(sent) == 1
    assert sent[0].session_id == "session-abc"
    assert sent[0].final is True
    assert "exceeds" in sent[0].content or "5 MB" in sent[0].content
