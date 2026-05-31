"""Tests for run.py: conversation persistence and disk-driven extraction."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.agent import conversation_store as cs
from src.agent.agent import Agent
from src.channels.base import IncomingMessage
from src.llm import LLMResponse


def _summary_response(slug: str, content: str = "Summary.") -> LLMResponse:
    return LLMResponse(
        text=json.dumps({
            "facts": [],
            "summary": {"topic_slug": slug, "content": content},
        }),
        tool_calls=None,
    )


def _history(user_count=3):
    msgs = []
    for i in range(user_count):
        msgs.append({"role": "user", "content": f"user message {i}"})
        msgs.append({"role": "assistant", "content": f"assistant reply {i}"})
    return msgs


@pytest.fixture
def memory_dir(agent_config):
    """Set up the memory dir with a taxonomy file."""
    mem = agent_config.context_dir / "memory"
    mem.mkdir(parents=True)
    (mem / "README.md").write_text("# Memory\n")
    return mem


async def _drive_worker_once(in_q: asyncio.Queue, out_q: asyncio.Queue, agent):
    import run as run_module
    task = asyncio.create_task(run_module.agent_worker(agent, in_q, out_q))
    await asyncio.wait_for(out_q.get(), timeout=2.0)
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# --- _extract_conversation: disk-driven single-conversation extraction ----

@pytest.mark.asyncio
async def test_extract_conversation_writes_summary_and_stamps_metadata(
    agent_config, memory_dir,
):
    from run import _extract_conversation

    agent = Agent(agent_config)
    cs.save(agent_config.context_dir, "sess-x", _history())

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock,
               return_value=_summary_response("first-topic")):
        await _extract_conversation(agent, "sess-x")

    record = cs.load(agent_config.context_dir, "sess-x")
    assert record["last_extracted_at"] is not None
    assert record["archive_path"].endswith("first-topic.md")
    archives = memory_dir / "archives" / "conversations"
    assert len(list(archives.glob("*.md"))) == 1


@pytest.mark.asyncio
async def test_extract_conversation_reuses_archive_path(agent_config, memory_dir):
    from run import _extract_conversation

    agent = Agent(agent_config)
    archives = memory_dir / "archives" / "conversations"
    cs.save(agent_config.context_dir, "sess-x", _history())

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock,
               return_value=_summary_response("first", "version 1")):
        await _extract_conversation(agent, "sess-x")
    first_path = cs.load(agent_config.context_dir, "sess-x")["archive_path"]

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock,
               return_value=_summary_response("different", "version 2")):
        await _extract_conversation(agent, "sess-x")

    assert cs.load(agent_config.context_dir, "sess-x")["archive_path"] == first_path
    assert len(list(archives.glob("*.md"))) == 1


@pytest.mark.asyncio
async def test_extract_conversation_missing_file_is_noop(agent_config, memory_dir):
    from run import _extract_conversation

    agent = Agent(agent_config)
    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock) as mock_llm:
        await _extract_conversation(agent, "does-not-exist")
        mock_llm.assert_not_called()


# --- periodic_extraction: disk-driven, gated ------------------------------

@pytest.mark.asyncio
async def test_extraction_pass_extracts_settled_conversation(agent_config, memory_dir):
    from run import _run_extraction_pass

    agent = Agent(agent_config)
    stale = datetime.now(timezone.utc) - timedelta(minutes=10)
    cs.save(agent_config.context_dir, "settled", _history(), now=stale)

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock,
               return_value=_summary_response("settled-topic")):
        await _run_extraction_pass(agent)

    assert cs.load(agent_config.context_dir, "settled")["last_extracted_at"] is not None


@pytest.mark.asyncio
async def test_extraction_pass_skips_fresh_conversation(agent_config, memory_dir):
    from run import _run_extraction_pass

    agent = Agent(agent_config)
    cs.save(agent_config.context_dir, "fresh", _history())  # updated just now

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock) as mock_llm:
        await _run_extraction_pass(agent)
        mock_llm.assert_not_called()

    assert cs.load(agent_config.context_dir, "fresh")["last_extracted_at"] is None


# --- delete (clear/reset): force-extract, then remove the transcript ------

@pytest.mark.asyncio
async def test_delete_command_extracts_then_removes_transcript(
    agent_config, memory_dir,
):
    archives = memory_dir / "archives" / "conversations"
    agent = Agent(agent_config)
    agent.sessions["cli"] = _history()
    cs.save(agent_config.context_dir, "cli", _history())

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    await in_q.put(IncomingMessage(
        content="", channel="cli", session_id="cli", reply_address={},
        command="clear",
    ))

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock,
               return_value=_summary_response("final", "final summary")):
        await _drive_worker_once(in_q, out_q, agent)

    # Transcript removed, but the memory summary survives.
    assert cs.load(agent_config.context_dir, "cli") is None
    assert "cli" not in agent.sessions
    mds = list(archives.glob("*.md"))
    assert len(mds) == 1
    assert "final summary" in mds[0].read_text()


@pytest.mark.asyncio
async def test_delete_command_extracts_disk_only_conversation(
    agent_config, memory_dir,
):
    """A conversation that is only on disk (not active in memory) is still
    summarized before deletion."""
    archives = memory_dir / "archives" / "conversations"
    agent = Agent(agent_config)
    cs.save(agent_config.context_dir, "past", _history())  # not in agent.sessions

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    await in_q.put(IncomingMessage(
        content="", channel="portal", session_id="past", reply_address={},
        command="clear",
    ))

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock,
               return_value=_summary_response("archived")):
        await _drive_worker_once(in_q, out_q, agent)

    assert cs.load(agent_config.context_dir, "past") is None
    assert len(list(archives.glob("*.md"))) == 1


# --- agent_worker persists the conversation after each turn ---------------

@pytest.mark.asyncio
async def test_agent_worker_persists_conversation_after_turn(agent_config, memory_dir):
    agent = Agent(agent_config)

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    await in_q.put(IncomingMessage(
        content="hello there", channel="cli", session_id="cli", reply_address={},
    ))

    with patch("src.agent.agent.call_llm", new_callable=AsyncMock,
               return_value=LLMResponse(text="hi back", tool_calls=None)):
        await _drive_worker_once(in_q, out_q, agent)

    record = cs.load(agent_config.context_dir, "cli")
    assert record is not None
    assert record["history"][0]["content"] == "hello there"
    assert record["history"][-1]["content"] == "hi back"


# --- extract command: force immediate extraction --------------------------

@pytest.mark.asyncio
async def test_extract_command_writes_summary(agent_config, memory_dir):
    archives = memory_dir / "archives" / "conversations"
    agent = Agent(agent_config)
    agent.sessions["cli"] = _history()

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    await in_q.put(IncomingMessage(
        content="", channel="cli", session_id="cli", reply_address={},
        command="extract",
    ))

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock,
               return_value=_summary_response("explicit-extract")):
        await _drive_worker_once(in_q, out_q, agent)

    mds = list(archives.glob("*.md"))
    assert len(mds) == 1
    assert mds[0].name.endswith("explicit-extract.md")
    # Session not removed by extract (only delete removes it).
    assert "cli" in agent.sessions


# --- slash dispatch (unchanged behaviour) ---------------------------------

@pytest.mark.asyncio
async def test_slash_help_is_dispatched_in_agent_worker(agent_config, memory_dir):
    agent = Agent(agent_config)
    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    await in_q.put(IncomingMessage(
        content="/help", channel="cli", session_id="cli",
        reply_address={}, command="slash",
    ))
    await _drive_worker_once(in_q, out_q, agent)
    assert out_q.empty()


@pytest.mark.asyncio
async def test_slash_clear_loops_through_in_queue(agent_config, memory_dir):
    """`/clear` enqueues a synthetic command=clear message that the next
    worker iteration processes — extracting and removing the conversation."""
    archives = memory_dir / "archives" / "conversations"
    agent = Agent(agent_config)
    agent.sessions["cli"] = _history()
    cs.save(agent_config.context_dir, "cli", _history())

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    await in_q.put(IncomingMessage(
        content="/clear", channel="cli", session_id="cli",
        reply_address={}, command="slash",
    ))

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock,
               return_value=_summary_response("cleared")):
        await _drive_worker_once(in_q, out_q, agent)

    assert "cli" not in agent.sessions
    assert cs.load(agent_config.context_dir, "cli") is None
    assert len(list(archives.glob("*.md"))) == 1


# --- scratch (ephemeral) session behavior ---------------------------------

@pytest.mark.asyncio
async def test_scratch_turn_is_not_persisted(agent_config, memory_dir):
    """A turn on the scratch session must never reach context/conversations/.

    The scratch slot lives only in agent.sessions so multi-turn works, but
    the disk store stays empty for it.
    """
    agent = Agent(agent_config)

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    await in_q.put(IncomingMessage(
        content="polish this", channel="portal", session_id="scratch",
        reply_address={},
    ))

    with patch("src.agent.agent.call_llm", new_callable=AsyncMock,
               return_value=LLMResponse(text="polished", tool_calls=None)):
        await _drive_worker_once(in_q, out_q, agent)

    assert cs.load(agent_config.context_dir, "scratch") is None
    # Active session still in memory so a follow-up turn has context.
    assert "scratch" in agent.sessions
    assert agent.sessions["scratch"][-1]["content"] == "polished"


@pytest.mark.asyncio
async def test_scratch_discard_pops_session_without_extracting(
    agent_config, memory_dir,
):
    """scratch_discard wipes in-memory state and never invokes extraction."""
    archives = memory_dir / "archives" / "conversations"
    agent = Agent(agent_config)
    agent.sessions["scratch"] = _history()

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    await in_q.put(IncomingMessage(
        content="", channel="portal", session_id="scratch", reply_address={},
        command="scratch_discard",
    ))

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock) as mock_llm:
        await _drive_worker_once(in_q, out_q, agent)
        mock_llm.assert_not_called()

    assert "scratch" not in agent.sessions
    assert not list(archives.glob("*.md"))
    assert cs.load(agent_config.context_dir, "scratch") is None


@pytest.mark.asyncio
async def test_scratch_discard_with_no_session_is_noop(agent_config, memory_dir):
    """A discard before scratch has ever been used must not error."""
    agent = Agent(agent_config)
    assert "scratch" not in agent.sessions

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    await in_q.put(IncomingMessage(
        content="", channel="portal", session_id="scratch", reply_address={},
        command="scratch_discard",
    ))

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock) as mock_llm:
        await _drive_worker_once(in_q, out_q, agent)
        mock_llm.assert_not_called()

    assert "scratch" not in agent.sessions


@pytest.mark.asyncio
async def test_clear_command_on_scratch_does_not_extract(agent_config, memory_dir):
    """Belt-and-braces: even if a `clear` arrives for scratch (legacy clients,
    misrouted frames), it must not summarize. Scratch never produces memory.
    """
    archives = memory_dir / "archives" / "conversations"
    agent = Agent(agent_config)
    agent.sessions["scratch"] = _history()

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    await in_q.put(IncomingMessage(
        content="", channel="portal", session_id="scratch", reply_address={},
        command="clear",
    ))

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock) as mock_llm:
        await _drive_worker_once(in_q, out_q, agent)
        mock_llm.assert_not_called()

    assert "scratch" not in agent.sessions
    assert not list(archives.glob("*.md"))


@pytest.mark.asyncio
async def test_slash_malformed_frame_is_dropped(agent_config, memory_dir, caplog):
    import logging
    import run as run_module

    agent = Agent(agent_config)
    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()

    for content in ("", "hello no slash", "/"):
        await in_q.put(IncomingMessage(
            content=content, channel="cli", session_id="cli",
            reply_address={}, command="slash",
        ))

    task = asyncio.create_task(run_module.agent_worker(agent, in_q, out_q))
    with caplog.at_level(logging.WARNING, logger="run"):
        for _ in range(50):
            if in_q.empty():
                break
            await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert out_q.empty()
    drops = [r for r in caplog.records if "malformed slash" in r.message]
    assert len(drops) == 3
