"""Tests for the standalone CLI client (cli.py).

Uses a real in-process websockets server rather than mocking the network layer.
"""
import asyncio
import io
import json

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
    """Return a Rich Console whose input() delivers *lines* then raises EOFError."""
    from rich.console import Console

    idx = [0]

    class _FakeConsole(Console):
        def input(self, prompt=""):  # noqa: A002
            if idx[0] < len(lines):
                val = lines[idx[0]]
                idx[0] += 1
                return val
            raise EOFError

    return _FakeConsole(file=io.StringIO(), stderr=False)


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
    assert received[0] == {"content": "hello world", "command": None}


@pytest.mark.asyncio
async def test_input_loop_sends_clear_command():
    """'/clear' is sent as {"content": "", "command": "clear"}."""
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
    assert received[0] == {"content": "", "command": "clear"}


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
