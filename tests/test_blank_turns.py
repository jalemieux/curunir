"""Blank turns and unknown portal frames never reach the model.

On 2026-11-06 the portal's new voice page streamed `{type: "audio"}` frames to
a container that predated voice. Each one was read as an empty chat message
and ran a full model turn: about 650 turns in 26 minutes.
"""
import asyncio

import pytest

from src.channels.base import IncomingMessage
from src.channels.portal import PortalChannel


def _counting_agent(calls: list):
    from src.config import AgentConfig
    agent = type("A", (), {})()
    agent.config = AgentConfig()
    agent.sessions = {}

    async def fake_handle(content, session_id, **kwargs):
        calls.append(content)
        return "ok"

    agent.handle = fake_handle
    return agent


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "   \n\t"])
async def test_agent_worker_drops_blank_turn(content):
    import run as run_module
    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    calls: list = []

    for text in (content, content, "real question"):
        await in_q.put(IncomingMessage(
            content=text, channel="portal", session_id="s", reply_address={},
        ))
    task = asyncio.create_task(run_module.agent_worker(_counting_agent(calls), in_q, out_q))
    while True:
        out = await asyncio.wait_for(out_q.get(), timeout=2.0)
        if out.final:
            break
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Only the real message ran a turn; the blank ones produced no reply.
    assert calls == ["real question"]
    assert out.content == "ok"


@pytest.mark.asyncio
async def test_portal_ignores_unknown_frame_type():
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(in_queue=in_q, url="ws://x", token="t")
    await ch._handle_user_message({"type": "some_future_frame", "data": "x", "session_id": "s"})
    assert in_q.qsize() == 0


@pytest.mark.asyncio
async def test_portal_chat_frame_still_enqueued():
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(in_queue=in_q, url="ws://x", token="t")
    await ch._handle_user_message({"content": "hello", "session_id": "s"})
    assert in_q.get_nowait().content == "hello"
