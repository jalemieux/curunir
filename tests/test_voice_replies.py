"""Voice turns: agent_worker attaches synthesized speech to the final reply."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.base import IncomingMessage


def _mock_agent(reply_text: str = "hello there"):
    agent = MagicMock()
    agent.sessions = {}
    agent.config.api_base = None
    agent.handle = AsyncMock(return_value=reply_text)
    return agent


def _incoming(voice: bool) -> IncomingMessage:
    return IncomingMessage(
        content="hi", channel="portal", session_id="s-1",
        reply_address={}, voice=voice,
    )


FAKE_ATT = {"filename": "voice.mp3", "path": "/tmp/voice.mp3",
            "mime_type": "audio/mpeg", "size": 3}


async def _run_one_turn(agent, msg, synth_result):
    from run import agent_worker
    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    await in_q.put(msg)
    with patch("run.synthesize_speech", new_callable=AsyncMock,
               return_value=synth_result) as synth:
        task = asyncio.create_task(agent_worker(agent, in_q, out_q))
        result = await asyncio.wait_for(out_q.get(), timeout=2.0)
        task.cancel()
    return result, synth


@pytest.mark.asyncio
async def test_voice_turn_attaches_mp3():
    result, synth = await _run_one_turn(
        _mock_agent(), _incoming(voice=True), (FAKE_ATT, None)
    )
    synth.assert_awaited_once()
    assert result.attachments == [FAKE_ATT]
    assert result.content == "hello there"
    assert result.final is True


@pytest.mark.asyncio
async def test_text_turn_does_not_synthesize():
    result, synth = await _run_one_turn(
        _mock_agent(), _incoming(voice=False), (FAKE_ATT, None)
    )
    assert synth.await_count == 0
    assert result.attachments is None


@pytest.mark.asyncio
async def test_voice_synth_failure_ships_text_only():
    result, synth = await _run_one_turn(
        _mock_agent(), _incoming(voice=True), (None, "tts exploded")
    )
    synth.assert_awaited_once()
    assert result.attachments is None
    assert result.content == "hello there"


@pytest.mark.asyncio
async def test_voice_turn_synthesizes_without_rewrite():
    result, synth = await _run_one_turn(
        _mock_agent(), _incoming(voice=True), (FAKE_ATT, None)
    )
    kwargs = synth.await_args.kwargs
    assert kwargs["rewrite"] is False
    assert kwargs["instructions"]


@pytest.mark.asyncio
async def test_voice_turn_passes_style_note_as_turn_note():
    agent = _mock_agent()
    await _run_one_turn(agent, _incoming(voice=True), (FAKE_ATT, None))
    kwargs = agent.handle.await_args.kwargs
    assert "voice note" in kwargs["turn_note"]
    sent = agent.handle.await_args.args[0]
    assert "[voice note" not in str(sent)


@pytest.mark.asyncio
async def test_text_turn_does_not_get_style_note():
    agent = _mock_agent()
    await _run_one_turn(agent, _incoming(voice=False), (FAKE_ATT, None))
    kwargs = agent.handle.await_args.kwargs
    assert kwargs["turn_note"] is None
