# tests/test_channels.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.base import IncomingMessage, OutgoingMessage
from src.channels.cli import CLIChannel, SESSION_ID
from src.channels.router import route_outbound


# --- Message type tests ---


def test_incoming_message_defaults():
    msg = IncomingMessage(content="hello", channel="cli", session_id="cli", reply_address={})
    assert msg.command is None


def test_incoming_message_with_command():
    msg = IncomingMessage(content="", channel="cli", session_id="cli", reply_address={}, command="clear")
    assert msg.command == "clear"


def test_incoming_message_attachments_default():
    msg = IncomingMessage(content="hello", channel="cli", session_id="cli", reply_address={})
    assert msg.attachments is None


def test_incoming_message_with_attachments():
    attachments = [{"filename": "report.pdf", "path": "/tmp/report.pdf", "mime_type": "application/pdf", "size": 1024}]
    msg = IncomingMessage(content="hello", channel="cli", session_id="cli", reply_address={}, attachments=attachments)
    assert msg.attachments == attachments
    assert msg.attachments[0]["filename"] == "report.pdf"


def test_outgoing_message_defaults():
    msg = OutgoingMessage(content="hi", channel="cli", session_id="cli", reply_address={})
    assert msg.tool_calls is None
    assert msg.final is True


def test_outgoing_message_with_tool_calls():
    msg = OutgoingMessage(content="hi", channel="cli", session_id="cli", reply_address={}, tool_calls=["read file"])
    assert msg.tool_calls == ["read file"]


# --- Router tests ---


@pytest.mark.asyncio
async def test_router_dispatches_to_correct_channel():
    out_queue = asyncio.Queue()
    mock_channel = AsyncMock()
    channels = {"cli": mock_channel}

    msg = OutgoingMessage(content="hello", channel="cli", session_id="cli", reply_address={})
    await out_queue.put(msg)

    task = asyncio.create_task(route_outbound(out_queue, channels))
    await asyncio.sleep(0.05)
    task.cancel()

    mock_channel.send.assert_called_once_with(msg)


@pytest.mark.asyncio
async def test_router_discards_unknown_channel(caplog):
    out_queue = asyncio.Queue()
    channels = {}

    msg = OutgoingMessage(content="hello", channel="slack", session_id="s1", reply_address={})
    await out_queue.put(msg)

    task = asyncio.create_task(route_outbound(out_queue, channels))
    await asyncio.sleep(0.05)
    task.cancel()

    assert "No channel registered for 'slack'" in caplog.text


@pytest.mark.asyncio
async def test_router_dispatches_multiple_messages():
    out_queue = asyncio.Queue()
    cli_channel = AsyncMock()
    slack_channel = AsyncMock()
    channels = {"cli": cli_channel, "slack": slack_channel}

    msg1 = OutgoingMessage(content="a", channel="cli", session_id="cli", reply_address={})
    msg2 = OutgoingMessage(content="b", channel="slack", session_id="s1", reply_address={})
    await out_queue.put(msg1)
    await out_queue.put(msg2)

    task = asyncio.create_task(route_outbound(out_queue, channels))
    await asyncio.sleep(0.05)
    task.cancel()

    cli_channel.send.assert_called_once_with(msg1)
    slack_channel.send.assert_called_once_with(msg2)


# --- CLIChannel tests ---


def test_cli_channel_session_id():
    assert SESSION_ID == "cli"


def test_cli_channel_constructor():
    q = asyncio.Queue()
    ch = CLIChannel(q)
    assert ch.in_queue is q
    assert ch.verbose is False
    assert ch._live is None


def test_cli_channel_injectable_console():
    q = asyncio.Queue()
    console = MagicMock()
    ch = CLIChannel(q, console=console)
    assert ch._console is console


@pytest.mark.asyncio
async def test_send_renders_markdown():
    console = MagicMock()
    ch = CLIChannel(asyncio.Queue(), console=console)
    msg = OutgoingMessage(content="**bold**", channel="cli", session_id="cli", reply_address={})

    await ch.send(msg)

    # Should have called print with a Markdown object
    assert console.print.call_count == 1
    from rich.markdown import Markdown
    assert isinstance(console.print.call_args[0][0], Markdown)


@pytest.mark.asyncio
async def test_send_shows_tool_calls():
    console = MagicMock()
    console.file = MagicMock()
    ch = CLIChannel(asyncio.Queue(), console=console)
    msg = OutgoingMessage(content="result", channel="cli", session_id="cli", reply_address={}, tool_calls=["Read x", "Write y"])

    await ch.send(msg)

    # 2 tool call lines (├─) + 1 flush (╰─) + 1 markdown content = 4 prints
    assert console.print.call_count == 4
    from rich.text import Text
    assert isinstance(console.print.call_args_list[0][0][0], Text)


@pytest.mark.asyncio
async def test_send_shows_tool_calls_always():
    console = MagicMock()
    console.file = MagicMock()
    ch = CLIChannel(asyncio.Queue(), console=console)
    msg = OutgoingMessage(content="result", channel="cli", session_id="cli", reply_address={}, tool_calls=["read x"])

    await ch.send(msg)

    # 1 tool call line (├─) + 1 flush (╰─) + 1 markdown content = 3 prints
    assert console.print.call_count == 3


@pytest.mark.asyncio
async def test_send_stops_spinner():
    console = MagicMock()
    ch = CLIChannel(asyncio.Queue(), console=console)
    # Simulate an active spinner
    mock_live = MagicMock()
    ch._live = mock_live

    msg = OutgoingMessage(content="done", channel="cli", session_id="cli", reply_address={})
    await ch.send(msg)

    mock_live.stop.assert_called_once()
    assert ch._live is None


async def _collect_and_ungate(ch, q, received):
    """Drain queue, collect messages, and ungate the input loop."""
    while True:
        msg = await q.get()
        received.append(msg)
        if msg.command is None:
            ch._ready.set()


@pytest.mark.asyncio
async def test_input_loop_pushes_message():
    console = MagicMock()
    console.input = MagicMock(side_effect=["hello world", EOFError])
    console.status = MagicMock(return_value=MagicMock())

    q = asyncio.Queue()
    ch = CLIChannel(q, console=console)

    received = []
    responder = asyncio.create_task(_collect_and_ungate(ch, q, received))
    await ch.start()
    responder.cancel()

    assert len(received) == 1
    assert received[0].content == "hello world"
    assert received[0].channel == "cli"
    assert received[0].session_id == SESSION_ID
    assert received[0].command is None


@pytest.mark.asyncio
async def test_input_loop_clear_command():
    console = MagicMock()
    console.input = MagicMock(side_effect=["/clear", EOFError])

    q = asyncio.Queue()
    ch = CLIChannel(q, console=console)

    await ch.start()

    # /clear doesn't gate _ready, so queue is not drained
    msg = q.get_nowait()
    assert msg.command == "clear"
    assert msg.content == ""


@pytest.mark.asyncio
async def test_input_loop_verbose_toggle():
    console = MagicMock()
    console.input = MagicMock(side_effect=["/verbose", "/verbose", EOFError])

    q = asyncio.Queue()
    ch = CLIChannel(q, console=console)

    await ch.start()

    assert ch.verbose is False  # toggled on then off
    # EOFError enqueues an extract command; verbose itself doesn't push
    msg = q.get_nowait()
    assert msg.command == "extract"
    assert q.empty()


@pytest.mark.asyncio
async def test_input_loop_skips_empty_lines():
    console = MagicMock()
    console.input = MagicMock(side_effect=["", "  ", EOFError])

    q = asyncio.Queue()
    ch = CLIChannel(q, console=console)

    await ch.start()

    # EOFError enqueues an extract command; empty lines don't
    msg = q.get_nowait()
    assert msg.command == "extract"
    assert q.empty()


@pytest.mark.asyncio
async def test_send_intermediate_does_not_set_ready():
    console = MagicMock()
    ch = CLIChannel(asyncio.Queue(), console=console)
    ch._ready.clear()

    msg = OutgoingMessage(content="", channel="cli", session_id="cli", reply_address={},
                          tool_calls=["Read foo.py"], final=False)
    ch.verbose = True
    await ch.send(msg)

    assert not ch._ready.is_set()


@pytest.mark.asyncio
async def test_send_sets_ready():
    console = MagicMock()
    ch = CLIChannel(asyncio.Queue(), console=console)
    ch._ready.clear()

    msg = OutgoingMessage(content="done", channel="cli", session_id="cli", reply_address={})
    await ch.send(msg)

    assert ch._ready.is_set()


@pytest.mark.asyncio
async def test_spinner_lifecycle():
    console = MagicMock()
    mock_status = MagicMock()
    console.status = MagicMock(return_value=mock_status)

    ch = CLIChannel(asyncio.Queue(), console=console)

    ch._start_spinner()
    assert ch._live is mock_status
    mock_status.start.assert_called_once()

    ch._stop_spinner()
    mock_status.stop.assert_called_once()
    assert ch._live is None


@pytest.mark.asyncio
async def test_start_spinner_stops_existing():
    console = MagicMock()
    old_live = MagicMock()
    new_status = MagicMock()
    console.status = MagicMock(return_value=new_status)

    ch = CLIChannel(asyncio.Queue(), console=console)
    ch._live = old_live

    ch._start_spinner()

    old_live.stop.assert_called_once()
    assert ch._live is new_status
