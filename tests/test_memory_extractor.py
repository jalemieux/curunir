# tests/test_memory_extractor.py
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.llm import LLMResponse
from src.memory_extractor import (
    _collect_existing_headings,
    _parse_first_heading,
    _replace_section,
    extract_learnings,
)


def _summary_response(slug: str, content: str = "Summary.") -> LLMResponse:
    return LLMResponse(
        text=json.dumps({
            "facts": [],
            "summary": {"topic_slug": slug, "content": content},
        }),
        tool_calls=None,
    )


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


@pytest.mark.asyncio
async def test_replaces_existing_section_by_heading(agent_config):
    """Same H2 heading should replace the existing section, not duplicate it."""
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "README.md").write_text("# Memory\n")

    target = mem_dir / "tasks.md"
    target.write_text(
        "## Adobe Agentic Role\n"
        "**Source:** chat - 2026-04-01\n"
        "**Fact:** old fact about Adobe role\n"
        "\n"
        "## Other Task\n"
        "**Fact:** unrelated\n"
    )

    new_block = (
        "## Adobe Agentic Role\n"
        "**Source:** chat - 2026-05-03\n"
        "**Fact:** new fact about Adobe role"
    )

    llm_response = LLMResponse(
        text=json.dumps({
            "facts": [{"file": "tasks.md", "content": new_block}],
            "summary": {"topic_slug": "test", "content": "summary"},
        }),
        tool_calls=None,
    )

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, return_value=llm_response):
        await extract_learnings(agent_config, _history())

    text = target.read_text()
    assert "old fact about Adobe role" not in text
    assert "new fact about Adobe role" in text
    assert text.count("## Adobe Agentic Role") == 1
    assert "## Other Task" in text
    assert "unrelated" in text


@pytest.mark.asyncio
async def test_appends_when_heading_is_new(agent_config):
    """Genuinely new headings should still be appended (existing behavior preserved)."""
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "README.md").write_text("# Memory\n")

    target = mem_dir / "tasks.md"
    target.write_text("## Existing Topic\n**Fact:** existing\n")

    new_block = "## Brand New Topic\n**Fact:** new content"
    llm_response = LLMResponse(
        text=json.dumps({
            "facts": [{"file": "tasks.md", "content": new_block}],
            "summary": {"topic_slug": "test", "content": "summary"},
        }),
        tool_calls=None,
    )
    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, return_value=llm_response):
        await extract_learnings(agent_config, _history())

    text = target.read_text()
    assert "## Existing Topic" in text
    assert "existing" in text
    assert "## Brand New Topic" in text
    assert "new content" in text


@pytest.mark.asyncio
async def test_prompt_includes_existing_headings(agent_config):
    """The system prompt should list existing H2 headings from memory files."""
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "README.md").write_text("# Memory\n")
    (mem_dir / "MEMORY.md").write_text("- index entry\n")
    (mem_dir / "tasks.md").write_text(
        "## Adobe Agentic Role\nfact1\n\n## X Listener\nfact2\n"
    )
    (mem_dir / "people").mkdir()
    (mem_dir / "people" / "alice.md").write_text("## Alice Background\nbio\n")

    archives = mem_dir / "archives" / "conversations"
    archives.mkdir(parents=True)
    (archives / "2026-05-03-something.md").write_text("## Should Not Appear\nblah\n")

    llm_response = LLMResponse(
        text=json.dumps({"facts": [], "summary": {"topic_slug": "test", "content": "s"}}),
        tool_calls=None,
    )
    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, return_value=llm_response) as mock_llm:
        await extract_learnings(agent_config, _history())

    mock_llm.assert_called_once()
    sys_prompt = mock_llm.call_args[0][1][0]["content"]
    assert "Adobe Agentic Role" in sys_prompt
    assert "X Listener" in sys_prompt
    assert "Alice Background" in sys_prompt
    assert "tasks.md" in sys_prompt
    assert "people/alice.md" in sys_prompt
    assert "Should Not Appear" not in sys_prompt


def test_parse_first_heading():
    assert _parse_first_heading("## Topic\nbody") == "Topic"
    assert _parse_first_heading("preamble\n\n## First\n**Fact:** x\n## Second\n") == "First"
    assert _parse_first_heading("no heading here") is None
    assert _parse_first_heading("### h3 only\n") is None
    assert _parse_first_heading("## Trimmed   \n") == "Trimmed"


def test_replace_section_at_end_of_file():
    text = "## A\nfact A\n\n## B\nfact B\n"
    new = "## B\nupdated B"
    result = _replace_section(text, "B", new)
    assert "## A" in result
    assert "fact A" in result
    assert "fact B" not in result
    assert "updated B" in result
    assert result.count("## B") == 1
    assert result.endswith("\n")


def test_replace_section_between_sections():
    text = "## A\nfact A\n\n## B\nfact B\n\n## C\nfact C\n"
    new = "## B\nupdated B"
    result = _replace_section(text, "B", new)
    assert "fact A" in result
    assert "fact B" not in result
    assert "updated B" in result
    assert "fact C" in result
    assert "## A" in result
    assert "## B" in result
    assert "## C" in result
    # Verify a blank line still separates ## B from ## C
    assert "updated B\n\n## C" in result


def test_replace_section_returns_none_when_heading_missing():
    text = "## A\nfact A\n"
    assert _replace_section(text, "Nonexistent", "## Nonexistent\nblah") is None


def test_replace_section_preserves_preamble():
    text = "preamble line\n\n## A\nfact A\n"
    new = "## A\nupdated"
    result = _replace_section(text, "A", new)
    assert result.startswith("preamble line\n")
    assert "updated" in result
    assert "fact A" not in result


def test_replace_section_h3_not_boundary():
    """`### ` lines should not terminate a section — only `## ` does."""
    text = "## A\nfact A\n\n### A subheading\nh3 content\n\n## B\nfact B\n"
    new = "## A\nupdated"
    result = _replace_section(text, "A", new)
    assert "updated" in result
    # H3 was part of section A — replaced along with it
    assert "h3 content" not in result
    assert "fact A" not in result
    # H2 "## B" was the real boundary — preserved
    assert "fact B" in result
    assert "## B" in result


def test_collect_existing_headings(tmp_path):
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    (mem_dir / "README.md").write_text("# README\n## ignore me\n")
    (mem_dir / "MEMORY.md").write_text("- index\n## also ignore\n")
    (mem_dir / "tasks.md").write_text("## Topic A\nbody\n\n## Topic B\nbody\n")
    (mem_dir / "people").mkdir()
    (mem_dir / "people" / "alice.md").write_text("## Alice\nbio\n")
    archives = mem_dir / "archives" / "conversations"
    archives.mkdir(parents=True)
    (archives / "x.md").write_text("## ignored archive\n")

    headings = _collect_existing_headings(mem_dir)

    assert headings.get("tasks.md") == ["Topic A", "Topic B"]
    assert headings.get("people/alice.md") == ["Alice"]
    assert "README.md" not in headings
    assert "MEMORY.md" not in headings
    assert not any("archives" in k for k in headings)


def test_collect_existing_headings_missing_dir(tmp_path):
    assert _collect_existing_headings(tmp_path / "does-not-exist") == {}


@pytest.mark.asyncio
async def test_extract_writes_timeline_and_topic_indexes(agent_config):
    """End-to-end: extraction writes facts, summary, AND progressive-discovery indexes."""
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "README.md").write_text("# Memory\n")

    llm_response = LLMResponse(
        text=json.dumps({
            "facts": [
                {"file": "projects.md", "content": "## Curunir\n**Fact:** memory-indexing in flight"},
                {"file": "people/anna.md", "content": "## Role\n**Fact:** PM"},
            ],
            "summary": {
                "topic_slug": "memory-indexing",
                "content": "Discussed progressive discovery design.",
            },
        }),
        tool_calls=None,
    )

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, return_value=llm_response):
        archive = await extract_learnings(agent_config, _history(user_count=2))

    assert archive is not None
    assert archive.exists()

    timeline = (mem_dir / "summaries" / "timeline.md").read_text()
    assert "[memory-indexing]" in timeline

    projects_topic = (mem_dir / "summaries" / "topics" / "projects.md").read_text()
    anna_topic = (mem_dir / "summaries" / "topics" / "people-anna.md").read_text()
    assert "[memory-indexing]" in projects_topic
    assert "[memory-indexing]" in anna_topic


@pytest.mark.asyncio
async def test_extract_skips_indexes_when_no_summary(agent_config):
    """If the LLM returns no summary, no archive or indexes are written."""
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "README.md").write_text("# Memory\n")

    llm_response = LLMResponse(
        text=json.dumps({"facts": [], "summary": None}),
        tool_calls=None,
    )

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, return_value=llm_response):
        result = await extract_learnings(agent_config, _history(user_count=2))

    assert result is None
    assert not (mem_dir / "summaries").exists()
