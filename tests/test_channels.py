# tests/test_channels.py
import asyncio
from unittest.mock import AsyncMock

import pytest

from src.channels.base import IncomingMessage, OutgoingMessage
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


def test_outgoing_message_delta_defaults_false():
    msg = OutgoingMessage(
        content="hi",
        channel="cli",
        session_id="s1",
        reply_address={},
    )
    assert msg.delta is False


def test_outgoing_message_delta_can_be_set():
    msg = OutgoingMessage(
        content="chunk",
        channel="cli",
        session_id="s1",
        reply_address={},
        delta=True,
    )
    assert msg.delta is True


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
async def test_agent_worker_passes_workflow_to_outgoing():
    """Workflow metadata from agent.handle() propagates to OutgoingMessage."""
    from unittest.mock import MagicMock

    agent = MagicMock()
    agent.sessions = {}

    async def fake_handle(content, session_id, **kwargs):
        metadata = kwargs.get("metadata")
        if metadata is not None:
            metadata["workflow"] = {"steps": ["plan", "build"], "current": "build"}
        return "done"

    agent.handle = AsyncMock(side_effect=fake_handle)

    in_q = asyncio.Queue()
    out_q = asyncio.Queue()

    msg = IncomingMessage(content="go", channel="cli", session_id="test", reply_address={})
    await in_q.put(msg)

    from run import agent_worker
    task = asyncio.create_task(agent_worker(agent, in_q, out_q))
    result = await asyncio.wait_for(out_q.get(), timeout=2.0)
    task.cancel()

    assert result.workflow == {"steps": ["plan", "build"], "current": "build"}


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

