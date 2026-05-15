"""Tests for run.py wiring of session-scoped archive path reuse."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

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


@pytest.mark.asyncio
async def test_extract_and_record_stores_path_first_time(agent_config, memory_dir):
    """First call records the archive path on the agent."""
    from run import _extract_and_record

    agent = Agent(agent_config)
    with patch(
        "src.memory_extractor.call_llm",
        new_callable=AsyncMock,
        return_value=_summary_response("first-topic"),
    ):
        await _extract_and_record(agent, "session-x", _history())

    assert "session-x" in agent.session_archives
    assert agent.session_archives["session-x"].name.endswith("first-topic.md")


@pytest.mark.asyncio
async def test_extract_and_record_reuses_path_on_second_call(agent_config, memory_dir):
    """Second call for same session writes to the same file."""
    from run import _extract_and_record

    agent = Agent(agent_config)
    archives = memory_dir / "archives" / "conversations"

    with patch(
        "src.memory_extractor.call_llm",
        new_callable=AsyncMock,
        return_value=_summary_response("first", "version 1"),
    ):
        await _extract_and_record(agent, "session-x", _history())

    first_path = agent.session_archives["session-x"]

    with patch(
        "src.memory_extractor.call_llm",
        new_callable=AsyncMock,
        return_value=_summary_response("totally-different", "version 2"),
    ):
        await _extract_and_record(agent, "session-x", _history())

    # Path is unchanged in the dict and only one file exists.
    assert agent.session_archives["session-x"] == first_path
    assert len(list(archives.glob("*.md"))) == 1
    assert "version 2" in first_path.read_text()


@pytest.mark.asyncio
async def test_extract_and_record_separate_sessions(agent_config, memory_dir):
    """Different sessions get distinct archive paths."""
    from run import _extract_and_record

    agent = Agent(agent_config)
    archives = memory_dir / "archives" / "conversations"

    with patch(
        "src.memory_extractor.call_llm",
        new_callable=AsyncMock,
        return_value=_summary_response("a"),
    ):
        await _extract_and_record(agent, "sess-a", _history())

    with patch(
        "src.memory_extractor.call_llm",
        new_callable=AsyncMock,
        return_value=_summary_response("b"),
    ):
        await _extract_and_record(agent, "sess-b", _history())

    assert agent.session_archives["sess-a"] != agent.session_archives["sess-b"]
    assert len(list(archives.glob("*.md"))) == 2


@pytest.mark.asyncio
async def test_extract_and_record_no_path_when_skipped(agent_config, memory_dir):
    """If extraction skips (short history), no path is recorded."""
    from run import _extract_and_record

    agent = Agent(agent_config)
    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock) as mock_llm:
        await _extract_and_record(agent, "session-x", _history(user_count=1))
        mock_llm.assert_not_called()

    assert "session-x" not in agent.session_archives


async def _drive_worker_once(in_q: asyncio.Queue, out_q: asyncio.Queue, agent):
    import run as run_module
    task = asyncio.create_task(run_module.agent_worker(agent, in_q, out_q))
    await asyncio.wait_for(out_q.get(), timeout=2.0)
    # Allow background extraction tasks to finish before cancelling.
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_clear_command_pops_archive_path_and_extracts(agent_config, memory_dir):
    """clear/reset pops the archive path mapping and triggers extraction."""
    archives = memory_dir / "archives" / "conversations"
    agent = Agent(agent_config)

    # Seed: prior extraction recorded a path for session "cli".
    seeded = archives / "2025-01-01-old.md"
    archives.mkdir(parents=True, exist_ok=True)
    seeded.write_text("# old\n\nold body\n")
    agent.session_archives["cli"] = seeded
    agent.sessions["cli"] = _history()

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    msg = IncomingMessage(
        content="", channel="cli", session_id="cli", reply_address={}, command="clear",
    )
    await in_q.put(msg)

    with patch(
        "src.memory_extractor.call_llm",
        new_callable=AsyncMock,
        return_value=_summary_response("final", "final summary"),
    ):
        await _drive_worker_once(in_q, out_q, agent)

    # Mapping is cleared so the next conversation starts fresh.
    assert "cli" not in agent.session_archives
    # The seeded file was overwritten in place rather than a new one created.
    assert seeded.exists()
    assert "final summary" in seeded.read_text()
    assert len(list(archives.glob("*.md"))) == 1


@pytest.mark.asyncio
async def test_slash_help_is_dispatched_in_agent_worker(agent_config, memory_dir):
    """A `command="slash"` message hits the slash branch in agent_worker and
    produces the /help table on out_queue — no LLM call, no channel knowledge
    of skills."""
    agent = Agent(agent_config)

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    await in_q.put(IncomingMessage(
        content="/help", channel="cli", session_id="cli",
        reply_address={}, command="slash",
    ))

    # No LLM patch needed — the slash branch returns before agent.handle().
    await _drive_worker_once(in_q, out_q, agent)

    # out_queue already drained by _drive_worker_once via its single get();
    # nothing further should have been queued.
    assert out_q.empty()


@pytest.mark.asyncio
async def test_slash_clear_loops_through_in_queue(agent_config, memory_dir):
    """`/clear` produces an enqueue (a synthetic IncomingMessage with
    command="clear"). On the next worker iteration that synthetic message
    hits the existing clear branch and emits the empty ack."""
    archives = memory_dir / "archives" / "conversations"
    agent = Agent(agent_config)
    agent.sessions["cli"] = _history()

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    await in_q.put(IncomingMessage(
        content="/clear", channel="cli", session_id="cli",
        reply_address={}, command="slash",
    ))

    with patch(
        "src.memory_extractor.call_llm",
        new_callable=AsyncMock,
        return_value=_summary_response("cleared"),
    ):
        # The first iteration handles the slash and re-enqueues a
        # command="clear" message; the second iteration processes that and
        # puts the empty ack on out_q, which _drive_worker_once waits for.
        await _drive_worker_once(in_q, out_q, agent)

    # Session was cleared.
    assert "cli" not in agent.sessions
    # Extraction ran on the cleared history.
    assert len(list(archives.glob("*.md"))) == 1


@pytest.mark.asyncio
async def test_extract_command_records_path(agent_config, memory_dir):
    """extract command records the archive path for future reuse."""
    archives = memory_dir / "archives" / "conversations"
    agent = Agent(agent_config)
    agent.sessions["cli"] = _history()

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    msg = IncomingMessage(
        content="", channel="cli", session_id="cli", reply_address={}, command="extract",
    )
    await in_q.put(msg)

    with patch(
        "src.memory_extractor.call_llm",
        new_callable=AsyncMock,
        return_value=_summary_response("explicit-extract"),
    ):
        await _drive_worker_once(in_q, out_q, agent)

    assert "cli" in agent.session_archives
    recorded = agent.session_archives["cli"]
    assert recorded.exists()
    assert recorded.name.endswith("explicit-extract.md")
    assert len(list(archives.glob("*.md"))) == 1
