"""Text segments (#577): the worker marks where a turn's streamed text is
split by tool rounds, so a streaming client can keep interim text instead of
overwriting it with the final reply."""
import asyncio
import json
from unittest.mock import patch

from run import TextSegments, agent_worker
from src.agent.agent import Agent
from src.channels.base import IncomingMessage, OutgoingMessage
from src.channels.local_web import LocalWebChannel
from src.llm import LLMResponse


def test_text_before_and_after_a_tool_round_are_different_segments():
    seg = TextSegments()
    assert [seg.text(), seg.text()] == [0, 0]
    seg.tool_call()
    seg.tool_call()  # a parallel batch is one boundary
    assert [seg.text(), seg.text()] == [1, 1]
    assert seg.final() == 1  # the reply is the segment that was streaming


def test_final_after_a_tool_round_is_a_new_segment():
    """An interrupt or error after tools: what streamed before is interim."""
    seg = TextSegments()
    seg.text()
    seg.tool_call()
    assert seg.final() == 1


def test_tool_rounds_without_text_open_no_segment():
    seg = TextSegments()
    seg.tool_call()
    assert seg.final() == 0  # nothing streamed: the reply is segment 0
    assert seg.text() == 0
    seg.tool_call()
    seg.tool_call()
    assert seg.text() == 1


def _tool_call(i: int) -> dict:
    return {
        "id": f"call_{i}", "type": "function",
        "function": {"name": "bash", "arguments": json.dumps({"command": f"echo {i}"})},
    }


async def _one_turn(agent_config, script) -> list[OutgoingMessage]:
    """Run agent_worker over one user message with a scripted, streaming LLM."""
    steps = iter(script)

    async def fake_llm(*args, on_text_delta=None, **kwargs):
        chunks, calls = next(steps)
        for chunk in chunks:
            await on_text_delta(chunk)
        return LLMResponse(text="".join(chunks) or None, tool_calls=calls)

    in_q, out_q = asyncio.Queue(), asyncio.Queue()
    await in_q.put(IncomingMessage(content="hi", channel="local_web", session_id="s1", reply_address={}))
    frames: list[OutgoingMessage] = []
    with patch("src.agent.agent.call_llm", new=fake_llm):
        task = asyncio.create_task(agent_worker(Agent(agent_config), in_q, out_q))
        try:
            while not frames or not frames[-1].final:
                frames.append(await asyncio.wait_for(out_q.get(), timeout=10))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    return frames


async def test_worker_stamps_segments_across_a_tool_round(agent_config):
    frames = await _one_turn(agent_config, [
        (["I'll check ", "first."], [_tool_call(1)]),
        ([], [_tool_call(2)]),
        (["All ", "done."], None),
    ])
    deltas = [(f.content, f.segment) for f in frames if f.delta]
    assert deltas == [("I'll check ", 0), ("first.", 0), ("All ", 1), ("done.", 1)]
    # tool-call frames are the boundary and carry no segment of their own
    assert [f.segment for f in frames if f.tool_calls] == [None, None]
    final = frames[-1]
    assert final.final and final.content == "All done." and final.segment == 1
    # existing fields are untouched
    assert all(f.final is False for f in frames[:-1])


async def test_worker_single_segment_without_tools(agent_config):
    frames = await _one_turn(agent_config, [(["Hel", "lo"], None)])
    assert [(f.content, f.segment) for f in frames] == [("Hel", 0), ("lo", 0), ("Hello", 0)]


async def test_local_web_frame_carries_segment_only_when_set(agent_config):
    channel = LocalWebChannel(asyncio.Queue(), agent_config, pairing_token="t")
    sent: list[dict] = []

    class _Socket:
        async def send_text(self, text):
            sent.append(json.loads(text))

    channel._socket = _Socket()
    base = dict(channel="local_web", session_id="s1", reply_address={})
    await channel.send(OutgoingMessage(content="a", delta=True, final=False, segment=0, **base))
    await channel.send(OutgoingMessage(content="", tool_calls=["bash: ls"], final=False, **base))
    await channel.send(OutgoingMessage(content="b", segment=2, **base))
    assert [f.get("segment") for f in sent] == [0, None, 2]
    assert "segment" not in sent[1]
    assert sent[0]["delta"] is True and sent[2]["final"] is True

