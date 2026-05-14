"""Tests for the standalone CLI client (cli.py).

Uses a real in-process websockets server rather than mocking the network layer.
"""
import asyncio
import io
import json
import os

import pytest
import websockets

# cli.py lives at the repo root; import it directly.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import cli  # noqa: E402

# Port range reserved for these tests — picked to avoid clashing with other tests
_BASE_PORT = 19000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_console_with_input(lines: list[str]):
    """Return a Rich Console and monkey-patch cli.PromptSession to deliver *lines*.

    Each call to the patched `PromptSession().prompt_async()` pops the next line
    and returns it; once exhausted, it raises EOFError so cli.run() exits cleanly.
    """
    from rich.console import Console

    idx = [0]

    class _FakePromptSession:
        def __init__(self, *args, **kwargs):
            pass

        async def prompt_async(self, prompt_text=None, **kwargs):
            if idx[0] < len(lines):
                val = lines[idx[0]]
                idx[0] += 1
                return val
            raise EOFError

    cli.PromptSession = _FakePromptSession  # type: ignore[attr-defined]
    return Console(file=io.StringIO(), stderr=False)


# ---------------------------------------------------------------------------
# Tests: input loop — what the client sends to the server
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_input_loop_sends_normal_message():
    """A regular line of text is sent as {"content": "...", "command": null}."""
    port = _BASE_PORT
    received: list[dict] = []

    async def handler(ws: websockets.ServerConnection) -> None:
        async for raw in ws:
            data = json.loads(raw)
            received.append(data)
            # Send a final:true reply so the client unblocks for EOF
            await ws.send(json.dumps({"content": "ok", "tool_calls": [], "final": True, "attachments": None}))

    console = _make_console_with_input(["hello world"])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    assert len(received) == 1
    assert received[0]["content"] == "hello world"
    assert received[0]["command"] is None
    assert not received[0].get("attachments")


@pytest.mark.asyncio
async def test_input_loop_sends_clear_as_slash_command():
    """'/clear' is sent as {"command": "slash", "text": "/clear"} — the
    server's slash dispatcher rewrites it into an internal clear."""
    port = _BASE_PORT + 1
    received: list[dict] = []

    async def handler(ws: websockets.ServerConnection) -> None:
        async for raw in ws:
            data = json.loads(raw)
            received.append(data)
            await ws.send(json.dumps({"content": "", "tool_calls": [], "final": True, "attachments": None}))

    console = _make_console_with_input(["/clear"])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    assert len(received) == 1
    assert received[0]["command"] == "slash"
    assert received[0]["text"] == "/clear"


@pytest.mark.asyncio
async def test_input_loop_skips_blank_lines():
    """Blank/whitespace-only input is not sent to the server."""
    port = _BASE_PORT + 2
    received: list[dict] = []

    async def handler(ws: websockets.ServerConnection) -> None:
        async for raw in ws:
            data = json.loads(raw)
            received.append(data)
            await ws.send(json.dumps({"content": "ok", "tool_calls": [], "final": True, "attachments": None}))

    # Two blank lines, then a real message, then EOF
    console = _make_console_with_input(["", "  ", "real message"])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    assert len(received) == 1
    assert received[0]["content"] == "real message"


@pytest.mark.asyncio
async def test_verbose_is_not_sent_to_server():
    """/verbose is handled locally and never sent over the wire."""
    port = _BASE_PORT + 3
    received: list[dict] = []

    async def handler(ws: websockets.ServerConnection) -> None:
        async for raw in ws:
            data = json.loads(raw)
            received.append(data)
            await ws.send(json.dumps({"content": "ok", "tool_calls": [], "final": True, "attachments": None}))

    console = _make_console_with_input(["/verbose", "hello"])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    # Only "hello" should have been sent — /verbose stays local
    assert len(received) == 1
    assert received[0]["content"] == "hello"


# ---------------------------------------------------------------------------
# Tests: output loop — what the client renders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_output_renders_markdown_content():
    """Agent content is printed to the console."""
    port = _BASE_PORT + 4

    async def handler(ws: websockets.ServerConnection) -> None:
        async for _ in ws:
            await ws.send(json.dumps({
                "content": "**bold answer**",
                "tool_calls": [],
                "final": True,
                "attachments": None,
            }))

    console = _make_console_with_input(["hi"])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    output = console.file.getvalue()
    assert "bold answer" in output


@pytest.mark.asyncio
async def test_output_renders_attachments():
    """Attachment filenames appear in console output."""
    port = _BASE_PORT + 5

    async def handler(ws: websockets.ServerConnection) -> None:
        async for _ in ws:
            await ws.send(json.dumps({
                "content": "See attached",
                "tool_calls": [],
                "final": True,
                "attachments": [{"filename": "report.pdf", "path": "/tmp/report.pdf"}],
            }))

    console = _make_console_with_input(["hi"])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    output = console.file.getvalue()
    assert "report.pdf" in output


@pytest.mark.asyncio
async def test_tool_calls_shown_by_default():
    """Tool calls ARE rendered by default (verbose is on)."""
    port = _BASE_PORT + 6

    async def handler(ws: websockets.ServerConnection) -> None:
        async for _ in ws:
            await ws.send(json.dumps({
                "content": "done",
                "tool_calls": ["Read secret_tool.py"],
                "final": True,
                "attachments": None,
            }))

    console = _make_console_with_input(["hi"])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    output = console.file.getvalue()
    assert "secret_tool.py" in output


@pytest.mark.asyncio
async def test_tool_calls_hidden_when_verbose_toggled_off():
    """/verbose toggles tool call display off (verbose is on by default)."""
    port = _BASE_PORT + 7

    async def handler(ws: websockets.ServerConnection) -> None:
        async for _ in ws:
            await ws.send(json.dumps({
                "content": "done",
                "tool_calls": ["Read hidden_tool.py"],
                "final": True,
                "attachments": None,
            }))

    # /verbose toggles off (default is on), then send message
    console = _make_console_with_input(["/verbose", "hi"])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    output = console.file.getvalue()
    assert "hidden_tool.py" not in output


@pytest.mark.asyncio
async def test_verbose_toggle_messages():
    """/verbose toggles off then on (verbose starts on by default)."""
    port = _BASE_PORT + 8
    call_count = [0]

    async def handler(ws: websockets.ServerConnection) -> None:
        async for _ in ws:
            call_count[0] += 1
            await ws.send(json.dumps({
                "content": f"response {call_count[0]}",
                "tool_calls": ["Read tool.py"],
                "final": True,
                "attachments": None,
            }))

    # Toggle off, send msg, toggle on, send msg
    console = _make_console_with_input(["/verbose", "msg1", "/verbose", "msg2"])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    output = console.file.getvalue()
    assert "Verbose mode off" in output
    assert "Verbose mode on" in output


# ---------------------------------------------------------------------------
# Tests: reconnection behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnects_after_server_drops_connection():
    """Client reconnects when the server closes the connection."""
    port = _BASE_PORT + 9
    connection_count = [0]

    async def handler(ws: websockets.ServerConnection) -> None:
        connection_count[0] += 1
        conn_num = connection_count[0]
        async for raw in ws:
            if conn_num == 1:
                # First connection: drop after receiving message
                await ws.close()
                return
            else:
                # Second connection: respond normally so client can exit
                await ws.send(json.dumps({
                    "content": "reconnected ok",
                    "tool_calls": [],
                    "final": True,
                    "attachments": None,
                }))

    console = _make_console_with_input(["first", "second"])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    assert connection_count[0] == 2
    output = console.file.getvalue()
    assert "reconnected ok" in output


@pytest.mark.asyncio
async def test_ready_event_resets_after_reconnect():
    """After reconnection the input gate is reset so the client can send again."""
    port = _BASE_PORT + 10
    connection_count = [0]
    all_received: list[dict] = []

    async def handler(ws: websockets.ServerConnection) -> None:
        connection_count[0] += 1
        conn_num = connection_count[0]
        async for raw in ws:
            data = json.loads(raw)
            all_received.append(data)
            if conn_num == 1:
                await ws.close()
                return
            else:
                await ws.send(json.dumps({
                    "content": "ok",
                    "tool_calls": [],
                    "final": True,
                    "attachments": None,
                }))

    console = _make_console_with_input(["first", "second"])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    contents = [m["content"] for m in all_received]
    assert "first" in contents
    assert "second" in contents


@pytest.mark.asyncio
async def test_output_renders_enriched_attachment_content():
    """When an attachment includes 'content', the CLI renders the markdown inline."""
    port = _BASE_PORT + 11

    async def handler(ws: websockets.ServerConnection) -> None:
        async for _ in ws:
            await ws.send(json.dumps({
                "content": "See the artifact",
                "tool_calls": [],
                "final": True,
                "attachments": [{
                    "filename": "analysis.md",
                    "path": "workspace/analysis.md",
                    "mime_type": "text/markdown",
                    "size": 20,
                    "content": "# Analysis\n\nKey findings here.",
                }],
            }))

    console = _make_console_with_input(["hi"])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    output = console.file.getvalue()
    assert "analysis.md" in output
    assert "Key findings here" in output


# ---------------------------------------------------------------------------
# Tests: CLI Staging helper
# ---------------------------------------------------------------------------
import base64 as _b64
import mimetypes

from cli import Staging


class TestStaging:
    def test_add_image_succeeds(self, tmp_path):
        img = tmp_path / "x.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        s = Staging()
        err = s.add(str(img))
        assert err is None
        payload = s.to_payload()
        assert len(payload) == 1
        assert payload[0]["filename"] == "x.png"
        assert payload[0]["mime_type"] == "image/png"
        assert _b64.b64decode(payload[0]["data"]).startswith(b"\x89PNG")

    def test_add_text_succeeds(self, tmp_path):
        p = tmp_path / "notes.txt"
        p.write_text("hello")
        s = Staging()
        assert s.add(str(p)) is None
        assert s.to_payload()[0]["mime_type"].startswith("text/")

    def test_add_missing_file_rejected(self, tmp_path):
        s = Staging()
        err = s.add(str(tmp_path / "nope.txt"))
        assert err and "not found" in err.lower()

    def test_add_oversized_image_rejected(self, tmp_path):
        p = tmp_path / "huge.png"
        p.write_bytes(b"\x00" * (5 * 1024 * 1024 + 1))
        s = Staging()
        err = s.add(str(p))
        assert err and "5" in err and "MB" in err

    def test_add_oversized_text_rejected(self, tmp_path):
        p = tmp_path / "huge.txt"
        p.write_bytes(b"x" * (256 * 1024 + 1))
        s = Staging()
        err = s.add(str(p))
        assert err and "256" in err and "KB" in err

    def test_total_cap_enforced(self, tmp_path):
        s = Staging()
        for i in range(4):
            p = tmp_path / f"a{i}.png"
            p.write_bytes(b"\x00" * (4 * 1024 * 1024))
            assert s.add(str(p)) is None
        p = tmp_path / "last.png"
        p.write_bytes(b"\x00" * (4 * 1024 * 1024 + 1))
        err = s.add(str(p))
        assert err and "20 MB" in err

    def test_binary_non_image_rejected(self, tmp_path):
        p = tmp_path / "weird.bin"
        p.write_bytes(b"\xff\xfe\xfa")
        s = Staging()
        err = s.add(str(p))
        assert err and ("UTF-8" in err or "recognized" in err.lower())

    def test_unknown_mime_but_utf8_accepted_as_text(self, tmp_path):
        p = tmp_path / "notes.weird"
        p.write_text("just text")
        assert mimetypes.guess_type(str(p))[0] is None
        s = Staging()
        assert s.add(str(p)) is None
        assert s.to_payload()[0]["mime_type"] == "text/plain"

    def test_remove_and_clear(self, tmp_path):
        p1 = tmp_path / "a.txt"
        p2 = tmp_path / "b.txt"
        p1.write_text("1")
        p2.write_text("2")
        s = Staging()
        s.add(str(p1))
        s.add(str(p2))
        assert len(s.to_payload()) == 2
        s.remove(1)
        assert [item["filename"] for item in s.to_payload()] == ["b.txt"]
        s.clear()
        assert s.to_payload() == []

    def test_remove_out_of_range_returns_error(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("1")
        s = Staging()
        s.add(str(p))
        assert s.remove(0) and "index" in s.remove(0).lower()
        assert s.remove(5) and "index" in s.remove(5).lower()


# ---------------------------------------------------------------------------
# Tests: /attach wiring in cli.run()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_attach_then_send_includes_attachments(tmp_path):
    port = _BASE_PORT + 20
    received: list[dict] = []

    async def handler(ws: websockets.ServerConnection) -> None:
        async for raw in ws:
            received.append(json.loads(raw))
            await ws.send(json.dumps({
                "content": "ok", "tool_calls": [], "final": True, "attachments": None,
            }))

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    console = _make_console_with_input([
        f"/attach {img}",
        "describe this",
    ])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    assert len(received) == 1
    payload = received[0]
    assert payload["content"] == "describe this"
    assert payload["command"] is None
    assert len(payload["attachments"]) == 1
    assert payload["attachments"][0]["filename"] == "x.png"
    assert payload["attachments"][0]["mime_type"] == "image/png"
    assert "data" in payload["attachments"][0]


@pytest.mark.asyncio
async def test_send_clears_staging(tmp_path):
    port = _BASE_PORT + 21
    received: list[dict] = []

    async def handler(ws):
        async for raw in ws:
            received.append(json.loads(raw))
            await ws.send(json.dumps({
                "content": "ok", "tool_calls": [], "final": True, "attachments": None,
            }))

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    console = _make_console_with_input([
        f"/attach {img}",
        "first send",
        "second send",
    ])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    assert len(received) == 2
    assert received[0]["attachments"] and len(received[0]["attachments"]) == 1
    assert not received[1].get("attachments")


@pytest.mark.asyncio
async def test_attach_clear_drops_staging_locally(tmp_path):
    port = _BASE_PORT + 22
    received: list[dict] = []

    async def handler(ws):
        async for raw in ws:
            received.append(json.loads(raw))
            await ws.send(json.dumps({
                "content": "ok", "tool_calls": [], "final": True, "attachments": None,
            }))

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    console = _make_console_with_input([
        f"/attach {img}",
        "/attach clear",
        "after clear",
    ])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    assert len(received) == 1
    assert received[0]["content"] == "after clear"
    assert not received[0].get("attachments")


@pytest.mark.asyncio
async def test_clear_command_also_clears_staging(tmp_path):
    port = _BASE_PORT + 23
    received: list[dict] = []

    async def handler(ws):
        async for raw in ws:
            received.append(json.loads(raw))
            await ws.send(json.dumps({
                "content": "ok", "tool_calls": [], "final": True, "attachments": None,
            }))

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    console = _make_console_with_input([
        f"/attach {img}",
        "/clear",
        "after reset",
    ])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    assert len(received) == 2
    # First wire message: /clear via slash dispatch; second: plain text with no attachments.
    assert received[0]["command"] == "slash"
    assert received[0]["text"] == "/clear"
    assert received[1]["content"] == "after reset"
    assert not received[1].get("attachments")


@pytest.mark.asyncio
async def test_attach_rejected_file_does_not_stage():
    port = _BASE_PORT + 24
    received: list[dict] = []

    async def handler(ws):
        async for raw in ws:
            received.append(json.loads(raw))
            await ws.send(json.dumps({
                "content": "ok", "tool_calls": [], "final": True, "attachments": None,
            }))

    console = _make_console_with_input([
        "/attach /nonexistent/path.png",
        "hi",
    ])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    assert len(received) == 1
    assert received[0]["content"] == "hi"
    assert not received[0].get("attachments")
    output = console.file.getvalue()
    assert "rejected" in output or "not found" in output


@pytest.mark.asyncio
async def test_detach_removes_staged_item(tmp_path):
    port = _BASE_PORT + 25
    received: list[dict] = []

    async def handler(ws):
        async for raw in ws:
            received.append(json.loads(raw))
            await ws.send(json.dumps({
                "content": "ok", "tool_calls": [], "final": True, "attachments": None,
            }))

    a = tmp_path / "a.txt"
    a.write_text("aaa")
    b = tmp_path / "b.txt"
    b.write_text("bbb")

    console = _make_console_with_input([
        f"/attach {a}",
        f"/attach {b}",
        "/detach 1",
        "go",
    ])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    assert len(received) == 1
    attachments = received[0]["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["filename"] == "b.txt"


@pytest.mark.asyncio
async def test_empty_text_with_staged_file_sends():
    port = _BASE_PORT + 26
    received: list[dict] = []

    async def handler(ws):
        async for raw in ws:
            received.append(json.loads(raw))
            await ws.send(json.dumps({
                "content": "ok", "tool_calls": [], "final": True, "attachments": None,
            }))

    import tempfile as _tf
    tmp = _tf.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    tmp.close()
    try:
        console = _make_console_with_input([
            f"/attach {tmp.name}",
            "",   # blank line — should still send because staging is non-empty
        ])

        async with websockets.serve(handler, "127.0.0.1", port):
            await cli.run("127.0.0.1", port, console=console)

        assert len(received) == 1
        assert received[0]["content"] == ""
        assert received[0]["attachments"]
    finally:
        os.unlink(tmp.name)


# ---------------------------------------------------------------------------
# Tests: drag-drop / auto-stage path detection
# ---------------------------------------------------------------------------
from cli import _extract_and_stage_paths


class _NullConsole:
    """Discard all console.print() output."""
    def print(self, *args, **kwargs):
        pass


class TestExtractAndStagePaths:
    def test_no_paths_returns_text_unchanged(self):
        s = Staging()
        assert _extract_and_stage_paths("hello world", s, _NullConsole()) == "hello world"
        assert s.to_payload() == []

    def test_path_to_nonexistent_file_preserved_in_text(self):
        s = Staging()
        txt = "look at /does/not/exist.png please"
        assert _extract_and_stage_paths(txt, s, _NullConsole()) == txt
        assert s.to_payload() == []

    def test_existing_path_is_staged_and_removed(self, tmp_path):
        p = tmp_path / "foo.txt"
        p.write_text("data")
        s = Staging()
        txt = f"read this: {p}"
        out = _extract_and_stage_paths(txt, s, _NullConsole())
        assert out == "read this:"
        assert len(s.to_payload()) == 1
        assert s.to_payload()[0]["filename"] == "foo.txt"

    def test_shell_escaped_spaces_in_path(self, tmp_path):
        p = tmp_path / "my file.txt"
        p.write_text("x")
        s = Staging()
        escaped = str(p).replace(" ", "\\ ")
        txt = f"open {escaped} now"
        out = _extract_and_stage_paths(txt, s, _NullConsole())
        assert out == "open now"
        assert len(s.to_payload()) == 1

    def test_multiple_paths_staged(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("a")
        b.write_text("b")
        s = Staging()
        txt = f"compare {a} and {b}"
        out = _extract_and_stage_paths(txt, s, _NullConsole())
        assert out == "compare and"
        assert {it["filename"] for it in s.to_payload()} == {"a.txt", "b.txt"}

    def test_path_only_message_becomes_empty_string(self, tmp_path):
        p = tmp_path / "x.txt"
        p.write_text("x")
        s = Staging()
        assert _extract_and_stage_paths(str(p), s, _NullConsole()) == ""
        assert len(s.to_payload()) == 1

    def test_rejected_file_stays_in_text(self, tmp_path):
        # File exists but exceeds 256 KB text cap → Staging.add returns error,
        # so we don't strip the path.
        big = tmp_path / "huge.txt"
        big.write_bytes(b"x" * (256 * 1024 + 1))
        s = Staging()
        txt = f"read {big}"
        out = _extract_and_stage_paths(txt, s, _NullConsole())
        assert str(big) in out
        assert s.to_payload() == []

    def test_unicode_whitespace_in_filename_is_matched(self, tmp_path):
        # macOS Finder inserts U+202F (NARROW NO-BREAK SPACE) before AM/PM in
        # screenshot filenames. Drag-drop sends it literally; the regex must
        # match through it rather than treating it as a token separator.
        nnbsp = " "
        name = f"Screenshot 10.58.12{nnbsp}PM.png"
        p = tmp_path / name
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        s = Staging()
        # Only the ASCII spaces are shell-escaped; the U+202F is passed literally.
        escaped = str(p).replace(" ", "\\ ")
        out = _extract_and_stage_paths(f"caption: {escaped}", s, _NullConsole())
        assert out == "caption:"
        assert len(s.to_payload()) == 1
        assert s.to_payload()[0]["filename"] == name


@pytest.mark.asyncio
async def test_drag_drop_path_autostaged_in_send(tmp_path):
    port = _BASE_PORT + 27
    received: list[dict] = []

    async def handler(ws):
        async for raw in ws:
            received.append(json.loads(raw))
            await ws.send(json.dumps({
                "content": "ok", "tool_calls": [], "final": True, "attachments": None,
            }))

    img = tmp_path / "screenshot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    console = _make_console_with_input([
        f"can you read this? {img}",
    ])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    assert len(received) == 1
    payload = received[0]
    assert payload["content"] == "can you read this?"
    assert payload["attachments"] and len(payload["attachments"]) == 1
    assert payload["attachments"][0]["filename"] == "screenshot.png"
    assert payload["attachments"][0]["mime_type"] == "image/png"
