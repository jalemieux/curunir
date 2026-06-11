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
from src.channels import portal as portal_mod
from src.channels.portal import PORTAL_SESSION_ID, PortalChannel
from src.skills import portal_skill_list


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
async def test_history_snapshot_enriches_attachments(portal_server, tmp_path):
    """A history message carrying an attachment (path only) is enriched before
    the snapshot is sent, so a reopened conversation's files are renderable
    without a second fetch."""
    doc = tmp_path / "report.txt"
    doc.write_text("the report body")
    in_q: asyncio.Queue = asyncio.Queue()

    def provider(sid: str) -> list[dict]:
        return [{
            "role": "assistant",
            "content": "see attached",
            "tool_calls": [],
            "attachments": [{
                "filename": "report.txt",
                "path": str(doc),
                "mime_type": "text/plain",
                "size": doc.stat().st_size,
            }],
        }]

    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        history_provider=provider,
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "user_message",
            "payload": {"command": "history_request", "session_id": "tab-A"},
        })
        raw = await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        msg = json.loads(raw)
        assert msg["type"] == "history_snapshot"
        att = msg["messages"][0]["attachments"][0]
        assert att["content"] == "the report body"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_slash_frame_enqueued_as_command_slash(portal_server):
    """Slash frames from the portal are forwarded onto the in_queue as
    IncomingMessage with command="slash" and the raw text as content.
    The channel does not interpret slash — agent_worker dispatches it."""
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "user_message",
            "payload": {"command": "slash", "text": "/help", "session_id": "tab-A"},
        })
        msg = await asyncio.wait_for(in_q.get(), timeout=2.0)
        assert msg.command == "slash"
        assert msg.content == "/help"
        assert msg.session_id == "tab-A"
        assert msg.channel == "portal"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_scratch_discard_frame_enqueued_as_command(portal_server):
    """The browser sends `scratch_discard` to drop the ephemeral session.

    The channel forwards it as an IncomingMessage with that command so the
    agent_worker can pop the in-memory session without persisting anything.
    """
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "user_message",
            "payload": {"command": "scratch_discard", "session_id": "scratch"},
        })
        msg = await asyncio.wait_for(in_q.get(), timeout=2.0)
        assert msg.command == "scratch_discard"
        assert msg.session_id == "scratch"
        assert msg.channel == "portal"
        assert msg.content == ""
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_slash_frame_preserves_arbitrary_text(portal_server):
    """The channel forwards the raw slash text verbatim — including args."""
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "user_message",
            "payload": {
                "command": "slash", "text": "/foo bar baz",
                "session_id": "tab-B",
            },
        })
        msg = await asyncio.wait_for(in_q.get(), timeout=2.0)
        assert msg.command == "slash"
        assert msg.content == "/foo bar baz"
        assert msg.session_id == "tab-B"
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


@pytest.mark.asyncio
async def test_outbound_buffered_while_disconnected_is_replayed_on_reconnect():
    """A reply sent while the socket is down — between a drop and the next
    reconnect — is buffered and delivered once the channel reconnects,
    instead of being silently swallowed (the original 1006-blip bug).
    """
    received: asyncio.Queue = asyncio.Queue()
    connections: list = []
    second_connected = asyncio.Event()

    async def handler(ws):
        connections.append(ws)
        if len(connections) == 1:
            # First connection: drop it with a retryable close code.
            await ws.close(code=1011, reason="bounce")
            return
        second_connected.set()
        try:
            async for raw in ws:
                await received.put(raw)
        except websockets.exceptions.ConnectionClosed:
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"ws://127.0.0.1:{port}/ws/agent"

    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(in_queue=in_q, url=url, token="t")
    task = asyncio.create_task(ch.start())
    try:
        # Wait until the first connection has been made and dropped, so the
        # channel is in reconnect backoff with _connection is None.
        for _ in range(500):
            if connections and ch._connection is None:
                break
            await asyncio.sleep(0.01)
        assert ch._connection is None

        await ch.send(OutgoingMessage(
            content="buffered hi", channel="portal",
            session_id="tab-7", reply_address={}, final=True,
        ))
        assert len(ch._outbound) == 1  # buffered, not dropped

        await asyncio.wait_for(second_connected.wait(), timeout=5.0)
        raw = await asyncio.wait_for(received.get(), timeout=5.0)
        msg = json.loads(raw)
        assert msg["type"] == "agent_message"
        assert msg["payload"]["content"] == "buffered hi"
        assert msg["payload"]["session_id"] == "tab-7"
        assert len(ch._outbound) == 0  # flushed on reconnect
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_streaming_deltas_are_not_buffered_when_disconnected(portal_server):
    """Deltas are ephemeral UI updates — the final message carries the full
    content — so a delta sent while disconnected is dropped, not queued.
    """
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
    )
    # Never started → _connection is None.
    await ch.send(OutgoingMessage(
        content="partial", channel="portal", session_id="s",
        reply_address={}, final=False, delta=True,
    ))
    assert len(ch._outbound) == 0
    # A final message in the same state IS buffered.
    await ch.send(OutgoingMessage(
        content="all of it", channel="portal", session_id="s",
        reply_address={}, final=True,
    ))
    assert len(ch._outbound) == 1


@pytest.mark.asyncio
async def test_duplicate_user_message_within_window_enqueued_once():
    """Two identical content frames back-to-back → exactly one
    IncomingMessage enqueued; the second is dropped as a duplicate."""
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(in_queue=in_q, url="ws://x", token="t")
    payload = {"content": "hello", "session_id": "tab-A"}
    await ch._handle_user_message(dict(payload))
    await ch._handle_user_message(dict(payload))
    assert in_q.qsize() == 1
    msg = in_q.get_nowait()
    assert msg.content == "hello"


@pytest.mark.asyncio
async def test_duplicate_user_message_after_window_both_enqueued(monkeypatch):
    """Identical content sent after the dedup window elapses is treated as
    a fresh message — both frames are enqueued."""
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(in_queue=in_q, url="ws://x", token="t")
    payload = {"content": "hello", "session_id": "tab-A"}

    clock = {"now": 1000.0}
    monkeypatch.setattr(portal_mod.time, "monotonic", lambda: clock["now"])

    await ch._handle_user_message(dict(payload))
    clock["now"] += portal_mod._DEDUP_WINDOW_SEC + 1.0
    await ch._handle_user_message(dict(payload))
    assert in_q.qsize() == 2


@pytest.mark.asyncio
async def test_different_content_within_window_both_enqueued():
    """Distinct messages within the window are not deduped."""
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(in_queue=in_q, url="ws://x", token="t")
    await ch._handle_user_message({"content": "hello", "session_id": "tab-A"})
    await ch._handle_user_message({"content": "goodbye", "session_id": "tab-A"})
    assert in_q.qsize() == 2


@pytest.mark.asyncio
async def test_same_content_different_sessions_both_enqueued():
    """Session id is part of the dedup hash, so identical text on two
    different sessions within the window is not deduped."""
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(in_queue=in_q, url="ws://x", token="t")
    await ch._handle_user_message({"content": "hello", "session_id": "tab-A"})
    await ch._handle_user_message({"content": "hello", "session_id": "tab-B"})
    assert in_q.qsize() == 2


@pytest.mark.asyncio
async def test_duplicate_scratch_discard_not_deduped():
    """Two rapid Start-overs (or a switch-away followed by another) must both
    reach the worker — dropping one could leave a ghost scratch session in
    agent.sessions on the server.
    """
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(in_queue=in_q, url="ws://x", token="t")
    payload = {"command": "scratch_discard", "session_id": "scratch"}
    await ch._handle_user_message(dict(payload))
    await ch._handle_user_message(dict(payload))
    assert in_q.qsize() == 2


@pytest.mark.asyncio
async def test_duplicate_interrupt_not_deduped():
    """Control frames bypass dedup — a duplicate interrupt still cancels
    each time (dropping one could leave a session running)."""
    in_q: asyncio.Queue = asyncio.Queue()
    seen: list[str] = []
    ch = PortalChannel(
        in_queue=in_q, url="ws://x", token="t",
        cancel_session=lambda sid: (seen.append(sid) or True),
    )
    payload = {"command": "interrupt", "session_id": "tab-A"}
    await ch._handle_user_message(dict(payload))
    await ch._handle_user_message(dict(payload))
    assert seen == ["tab-A", "tab-A"]


@pytest.mark.asyncio
async def test_skills_request_invokes_provider_and_sends_snapshot(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    fake_skills = [
        {"name": "memo", "display_name": "Memo", "summary": "A memo"},
    ]

    def provider() -> list[dict]:
        return fake_skills

    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        skills_provider=provider,
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({"type": "skills_request"})
        raw = await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        msg = json.loads(raw)
        assert msg["type"] == "skills_snapshot"
        assert msg["skills"] == fake_skills
        assert msg["session_id"] == PORTAL_SESSION_ID
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_skills_request_command_triggers_snapshot(portal_server):
    """Browser-driven: a user_message with command=skills_request and a
    session_id triggers a skills_snapshot tagged with that session."""
    in_q: asyncio.Queue = asyncio.Queue()

    def provider() -> list[dict]:
        return []

    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        skills_provider=provider,
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "user_message",
            "payload": {"command": "skills_request", "session_id": "tab-S"},
        })
        raw = await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        msg = json.loads(raw)
        assert msg["type"] == "skills_snapshot"
        assert msg["session_id"] == "tab-S"
        assert msg["skills"] == []
    finally:
        task.cancel()


def _write_portal_skill(parent, name, summary):
    """Create skills/<name>/SKILL.md with a portal_summary (browse-panel gate)."""
    d = parent / name
    d.mkdir()
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: agent desc for {name}\n"
        f'portal_summary: "{summary}"\n---\n# {name}\n'
    )


@pytest.mark.asyncio
async def test_skills_snapshot_respects_persona_allowlist(portal_server, tmp_path):
    """The channel-level guard the issue asks for: build the skills_provider
    exactly as run.py does — from a persona-style allowlist — and assert the
    skills_snapshot the browser receives contains only allowlisted skills.

    Closes the gap between the passing portal_skill_list unit test and the
    failing finance deployment: if the provider ever drops the allowlist arg
    (the regression), an out-of-allowlist skill like `superheroes` leaks into
    the panel and this test fails.
    """
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    # In-allowlist, finance-style panel skills.
    for name in ("balance-sheet", "financial-analysis"):
        _write_portal_skill(skills_dir, name, f"{name} for the panel")
    # Out-of-allowlist skills that ALSO set portal_summary — these must not
    # appear once the allowlist is applied.
    for name in ("superheroes", "gemini-image"):
        _write_portal_skill(skills_dir, name, f"{name} for the panel")

    allowlist = ["balance-sheet", "financial-analysis"]
    # Construct the provider the SAME way run.py does (run.py:722-725).
    provider = lambda: portal_skill_list(
        [skills_dir], set(allowlist) if allowlist else None
    )

    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        skills_provider=provider,
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({"type": "skills_request"})
        raw = await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        msg = json.loads(raw)
        assert msg["type"] == "skills_snapshot"
        names = [s["name"] for s in msg["skills"]]
        assert names == ["balance-sheet", "financial-analysis"]
        assert "superheroes" not in names
        assert "gemini-image" not in names
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_conversations_request_invokes_provider_and_sends_snapshot(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    fake_convos = [
        {"session_id": "tab-A", "title": "First", "preview": "First",
         "created_at": "2026-05-01T00:00:00+00:00",
         "updated_at": "2026-05-02T00:00:00+00:00"},
    ]

    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        conversations_provider=lambda: fake_convos,
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({"type": "conversations_request"})
        raw = await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        msg = json.loads(raw)
        assert msg["type"] == "conversations_snapshot"
        assert msg["conversations"] == fake_convos
        assert msg["session_id"] == PORTAL_SESSION_ID
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_conversations_request_command_triggers_snapshot(portal_server):
    """Browser-driven: a user_message with command=conversations_request and a
    session_id triggers a conversations_snapshot tagged with that session."""
    in_q: asyncio.Queue = asyncio.Queue()

    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        conversations_provider=lambda: [],
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "user_message",
            "payload": {"command": "conversations_request", "session_id": "tab-S"},
        })
        raw = await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        msg = json.loads(raw)
        assert msg["type"] == "conversations_snapshot"
        assert msg["session_id"] == "tab-S"
        assert msg["conversations"] == []
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_conversations_request_no_connection_no_send(portal_server):
    """A conversations_request with no live connection produces no send."""
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        conversations_provider=lambda: [{"session_id": "x"}],
    )
    # No start() — _connection is None.
    await ch._handle_conversations_request({})
    assert ch._connection is None
