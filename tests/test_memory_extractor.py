# tests/test_memory_extractor.py
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.llm import LLMResponse
from src.memory_extractor import extract_learnings


def _history(user_count=3):
    """Build a minimal conversation history with N user messages."""
    msgs = []
    for i in range(user_count):
        msgs.append({"role": "user", "content": f"user message {i}"})
        msgs.append({"role": "assistant", "content": f"assistant reply {i}"})
    return msgs


@pytest.mark.asyncio
async def test_skips_short_history(agent_config):
    """Should skip extraction when fewer than 2 user messages."""
    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock) as mock_llm:
        await extract_learnings(agent_config, _history(user_count=1))
        mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_calls_llm_with_history(agent_config):
    """Should call LLM with extraction prompt when enough messages."""
    # Set up memory dir
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "README.md").write_text("# Memory\n")

    llm_response = LLMResponse(
        text=json.dumps({"facts": [], "summary": {"topic_slug": "test", "content": "A test conversation"}}),
        tool_calls=None,
    )

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, return_value=llm_response) as mock_llm:
        await extract_learnings(agent_config, _history(user_count=2))
        mock_llm.assert_called_once()
        args = mock_llm.call_args
        assert args[0][0] == agent_config.model
        assert len(args[0][1]) == 2  # system + user messages


@pytest.mark.asyncio
async def test_writes_facts_to_files(agent_config):
    """Should write extracted facts to the correct memory files."""
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "README.md").write_text("# Memory\n")

    llm_response = LLMResponse(
        text=json.dumps({
            "facts": [
                {"file": "preferences.md", "content": "## Test\n**Fact:** likes testing"},
            ],
            "summary": {"topic_slug": "test-conv", "content": "Discussed testing."},
        }),
        tool_calls=None,
    )

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, return_value=llm_response):
        await extract_learnings(agent_config, _history())

    prefs = mem_dir / "preferences.md"
    assert prefs.exists()
    assert "likes testing" in prefs.read_text()


@pytest.mark.asyncio
async def test_appends_to_existing_file(agent_config):
    """Should append to existing memory files rather than overwriting."""
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "README.md").write_text("# Memory\n")

    existing = mem_dir / "preferences.md"
    existing.write_text("## Existing\n**Fact:** original content\n")

    llm_response = LLMResponse(
        text=json.dumps({
            "facts": [{"file": "preferences.md", "content": "## New\n**Fact:** new content"}],
            "summary": {"topic_slug": "test", "content": "Summary."},
        }),
        tool_calls=None,
    )

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, return_value=llm_response):
        await extract_learnings(agent_config, _history())

    text = existing.read_text()
    assert "original content" in text
    assert "new content" in text


@pytest.mark.asyncio
async def test_rejects_path_traversal(agent_config):
    """Should reject file paths that escape the memory directory."""
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "README.md").write_text("# Memory\n")

    llm_response = LLMResponse(
        text=json.dumps({
            "facts": [{"file": "../../etc/passwd", "content": "malicious"}],
            "summary": {"topic_slug": "test", "content": "Summary."},
        }),
        tool_calls=None,
    )

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, return_value=llm_response):
        await extract_learnings(agent_config, _history())

    # The malicious file should NOT have been written
    assert not (agent_config.context_dir / ".." / "etc" / "passwd").exists()


@pytest.mark.asyncio
async def test_writes_conversation_summary(agent_config):
    """Should write conversation summary to archives."""
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "README.md").write_text("# Memory\n")

    llm_response = LLMResponse(
        text=json.dumps({
            "facts": [],
            "summary": {"topic_slug": "design-review", "content": "Reviewed the design."},
        }),
        tool_calls=None,
    )

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, return_value=llm_response):
        await extract_learnings(agent_config, _history())

    archives = mem_dir / "archives" / "conversations"
    assert archives.exists()
    files = list(archives.glob("*design-review.md"))
    assert len(files) == 1
    assert "Reviewed the design" in files[0].read_text()


@pytest.mark.asyncio
async def test_handles_unparseable_json(agent_config):
    """Should not raise when LLM returns invalid JSON."""
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)

    llm_response = LLMResponse(text="not valid json at all", tool_calls=None)

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, return_value=llm_response):
        # Should not raise
        await extract_learnings(agent_config, _history())


@pytest.mark.asyncio
async def test_handles_llm_exception(agent_config):
    """Should not raise when LLM call fails."""
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        # Should not raise
        await extract_learnings(agent_config, _history())


@pytest.mark.asyncio
async def test_creates_subdirectory_for_people(agent_config):
    """Should create subdirectories as needed (e.g., people/)."""
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "README.md").write_text("# Memory\n")

    llm_response = LLMResponse(
        text=json.dumps({
            "facts": [{"file": "people/alice.md", "content": "## Alice\n**Fact:** works on infra"}],
            "summary": {"topic_slug": "test", "content": "Summary."},
        }),
        tool_calls=None,
    )

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, return_value=llm_response):
        await extract_learnings(agent_config, _history())

    assert (mem_dir / "people" / "alice.md").exists()
    assert "works on infra" in (mem_dir / "people" / "alice.md").read_text()
