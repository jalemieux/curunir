# tests/test_memory_extractor.py
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.llm import LLMResponse
from src.memory_extractor import extract_learnings


def _llm(text: str) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=None)


def _extraction(facts, slug="test", summary="Summary.") -> LLMResponse:
    """An extraction-pass LLM response."""
    return _llm(json.dumps({
        "facts": facts,
        "summary": {"topic_slug": slug, "content": summary},
    }))


def _history(user_count=3):
    """Build a minimal conversation history with N user messages."""
    msgs = []
    for i in range(user_count):
        msgs.append({"role": "user", "content": f"user message {i}"})
        msgs.append({"role": "assistant", "content": f"assistant reply {i}"})
    return msgs


def _mem_dir(agent_config):
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "README.md").write_text("# Memory\n")
    return mem_dir


@pytest.mark.asyncio
async def test_skips_short_history(agent_config):
    """Should skip extraction when fewer than 2 user messages."""
    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock) as mock_llm:
        await extract_learnings(agent_config, _history(user_count=1))
        mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_calls_llm_with_history(agent_config):
    """Should call LLM with extraction prompt when enough messages."""
    _mem_dir(agent_config)

    with patch(
        "src.memory_extractor.call_llm",
        new_callable=AsyncMock,
        return_value=_extraction([]),
    ) as mock_llm:
        await extract_learnings(agent_config, _history(user_count=2))
        mock_llm.assert_called_once()
        args = mock_llm.call_args
        assert args[0][0] == agent_config.model
        assert len(args[0][1]) == 2  # system + user messages


@pytest.mark.asyncio
async def test_writes_facts_to_files(agent_config):
    """Should write consolidated facts to the correct memory files."""
    mem_dir = _mem_dir(agent_config)

    side_effect = [
        _extraction([
            {"file": "preferences.md", "content": "## Test\n**Fact:** likes testing"},
        ], slug="test-conv", summary="Discussed testing."),
        _llm("## Test\n**Fact:** likes testing\n"),
    ]

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, side_effect=side_effect):
        await extract_learnings(agent_config, _history())

    prefs = mem_dir / "preferences.md"
    assert prefs.exists()
    assert "likes testing" in prefs.read_text()


@pytest.mark.asyncio
async def test_merges_near_duplicate_entries(agent_config):
    """Consolidation should merge near-duplicate entries into one."""
    mem_dir = _mem_dir(agent_config)
    tasks = mem_dir / "tasks.md"
    tasks.write_text("## Adobe Role\n**Fact:** applied for Adobe agentic role\n")

    merged = "## Adobe Role\n**Fact:** applied for and interviewing for Adobe agentic role\n"
    side_effect = [
        _extraction([
            {"file": "tasks.md", "content": "## Adobe\n**Fact:** interviewing for Adobe agentic role"},
        ]),
        _llm(merged),
    ]

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, side_effect=side_effect):
        await extract_learnings(agent_config, _history())

    text = tasks.read_text()
    assert text.count("## Adobe") == 1
    assert "interviewing" in text


@pytest.mark.asyncio
async def test_preserves_distinct_facts(agent_config):
    """Consolidation output that keeps distinct facts is written verbatim."""
    mem_dir = _mem_dir(agent_config)
    projects = mem_dir / "projects.md"
    projects.write_text("## Curunir\n**Fact:** memory indexing in flight\n")

    merged = (
        "## Curunir\n**Fact:** memory indexing in flight\n\n"
        "## Portal\n**Fact:** admin token re-reveal shipped\n"
    )
    side_effect = [
        _extraction([
            {"file": "projects.md", "content": "## Portal\n**Fact:** admin token re-reveal shipped"},
        ]),
        _llm(merged),
    ]

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, side_effect=side_effect):
        await extract_learnings(agent_config, _history())

    text = projects.read_text()
    assert "memory indexing in flight" in text
    assert "admin token re-reveal shipped" in text


@pytest.mark.asyncio
async def test_prunes_resolved_fact(agent_config):
    """A fact the conversation marks resolved is dropped by consolidation."""
    mem_dir = _mem_dir(agent_config)
    tasks = mem_dir / "tasks.md"
    tasks.write_text(
        "## Fix Login Bug\n**Fact:** login bug needs fixing\n\n"
        "## Write Docs\n**Fact:** docs still pending\n"
    )

    merged = "## Write Docs\n**Fact:** docs still pending\n"
    side_effect = [
        _extraction([
            {"file": "tasks.md", "content": "## Login\n**Fact:** login bug fixed and shipped"},
        ]),
        _llm(merged),
    ]

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, side_effect=side_effect):
        await extract_learnings(agent_config, _history())

    text = tasks.read_text()
    assert "login bug needs fixing" not in text
    assert "docs still pending" in text


@pytest.mark.asyncio
async def test_snapshot_created_with_prior_content(agent_config):
    """Before a rewrite, prior file content is snapshotted to memory-snapshots/."""
    mem_dir = _mem_dir(agent_config)
    tasks = mem_dir / "tasks.md"
    prior = "## Old\n**Fact:** prior content\n"
    tasks.write_text(prior)

    side_effect = [
        _extraction([{"file": "tasks.md", "content": "## New\n**Fact:** new"}]),
        _llm("## New\n**Fact:** new\n"),
    ]

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, side_effect=side_effect):
        await extract_learnings(agent_config, _history())

    snap_dir = mem_dir / "archives" / "memory-snapshots"
    assert snap_dir.exists()
    snaps = list(snap_dir.glob("*-tasks.md"))
    assert len(snaps) == 1
    assert snaps[0].read_text() == prior


@pytest.mark.asyncio
async def test_no_snapshot_for_new_file(agent_config):
    """A brand-new file has no prior content, so no snapshot is written."""
    mem_dir = _mem_dir(agent_config)

    side_effect = [
        _extraction([{"file": "new.md", "content": "## Topic\n**Fact:** x"}]),
        _llm("## Topic\n**Fact:** x\n"),
    ]

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, side_effect=side_effect):
        await extract_learnings(agent_config, _history())

    assert (mem_dir / "new.md").exists()
    assert not (mem_dir / "archives" / "memory-snapshots").exists()


@pytest.mark.asyncio
async def test_consolidation_on_brand_new_file(agent_config):
    """Consolidation creates a file that did not exist before."""
    mem_dir = _mem_dir(agent_config)

    merged = "## Alice\n**Fact:** works on infra\n"
    side_effect = [
        _extraction([{"file": "people/alice.md", "content": "## Alice\n**Fact:** works on infra"}]),
        _llm(merged),
    ]

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, side_effect=side_effect):
        await extract_learnings(agent_config, _history())

    alice = mem_dir / "people" / "alice.md"
    assert alice.exists()
    assert "works on infra" in alice.read_text()


@pytest.mark.asyncio
async def test_strips_code_fence_from_consolidation(agent_config):
    """A code-fenced consolidation response is unwrapped before writing."""
    mem_dir = _mem_dir(agent_config)

    side_effect = [
        _extraction([{"file": "tasks.md", "content": "## T\n**Fact:** x"}]),
        _llm("```markdown\n## T\n**Fact:** x\n```"),
    ]

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, side_effect=side_effect):
        await extract_learnings(agent_config, _history())

    text = (mem_dir / "tasks.md").read_text()
    assert "```" not in text
    assert text.startswith("## T")


@pytest.mark.asyncio
async def test_consolidation_failure_falls_back_to_append(agent_config):
    """When consolidation LLM call fails, raw facts are appended (no data loss)."""
    mem_dir = _mem_dir(agent_config)
    tasks = mem_dir / "tasks.md"
    tasks.write_text("## Existing\n**Fact:** original content\n")

    async def fake_llm(model, messages, tools, **kwargs):
        # First call is extraction; subsequent calls (consolidation) blow up.
        if "memory extraction system" in messages[0]["content"]:
            return _extraction([
                {"file": "tasks.md", "content": "## New\n**Fact:** new content"},
            ])
        raise RuntimeError("consolidation boom")

    with patch("src.memory_extractor.call_llm", new=fake_llm):
        await extract_learnings(agent_config, _history())

    text = tasks.read_text()
    assert "original content" in text
    assert "new content" in text


@pytest.mark.asyncio
async def test_consolidation_empty_response_falls_back_to_append(agent_config):
    """An empty consolidation response also falls back to append."""
    mem_dir = _mem_dir(agent_config)
    tasks = mem_dir / "tasks.md"
    tasks.write_text("## Existing\n**Fact:** original\n")

    side_effect = [
        _extraction([{"file": "tasks.md", "content": "## New\n**Fact:** appended fact"}]),
        _llm(""),
    ]

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, side_effect=side_effect):
        await extract_learnings(agent_config, _history())

    text = tasks.read_text()
    assert "original" in text
    assert "appended fact" in text


@pytest.mark.asyncio
async def test_rejects_path_traversal(agent_config):
    """Should reject file paths that escape the memory directory."""
    _mem_dir(agent_config)

    side_effect = [
        _extraction([{"file": "../../etc/passwd", "content": "## X\nmalicious"}]),
        _llm("## X\nmalicious\n"),
    ]

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, side_effect=side_effect):
        await extract_learnings(agent_config, _history())

    assert not (agent_config.context_dir / ".." / "etc" / "passwd").exists()


@pytest.mark.asyncio
async def test_writes_conversation_summary(agent_config):
    """Should write conversation summary to archives."""
    mem_dir = _mem_dir(agent_config)

    with patch(
        "src.memory_extractor.call_llm",
        new_callable=AsyncMock,
        return_value=_extraction([], slug="design-review", summary="Reviewed the design."),
    ):
        await extract_learnings(agent_config, _history())

    archives = mem_dir / "archives" / "conversations"
    assert archives.exists()
    files = list(archives.glob("*design-review.md"))
    assert len(files) == 1
    assert "Reviewed the design" in files[0].read_text()


@pytest.mark.asyncio
async def test_handles_unparseable_json(agent_config):
    """Should not raise when LLM returns invalid JSON."""
    (agent_config.context_dir / "memory").mkdir(parents=True)

    with patch(
        "src.memory_extractor.call_llm",
        new_callable=AsyncMock,
        return_value=_llm("not valid json at all"),
    ):
        await extract_learnings(agent_config, _history())


@pytest.mark.asyncio
async def test_handles_llm_exception(agent_config):
    """Should not raise when LLM call fails."""
    (agent_config.context_dir / "memory").mkdir(parents=True)

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        await extract_learnings(agent_config, _history())


@pytest.mark.asyncio
async def test_extract_writes_timeline_and_topic_indexes(agent_config):
    """End-to-end: extraction writes facts, summary, AND progressive-discovery indexes."""
    mem_dir = _mem_dir(agent_config)

    side_effect = [
        _extraction(
            [
                {"file": "projects.md", "content": "## Curunir\n**Fact:** memory-indexing in flight"},
                {"file": "people/anna.md", "content": "## Role\n**Fact:** PM"},
            ],
            slug="memory-indexing",
            summary="Discussed progressive discovery design.",
        ),
        _llm("## Curunir\n**Fact:** memory-indexing in flight\n"),
        _llm("## Role\n**Fact:** PM\n"),
    ]

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, side_effect=side_effect):
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
    mem_dir = _mem_dir(agent_config)

    with patch(
        "src.memory_extractor.call_llm",
        new_callable=AsyncMock,
        return_value=_llm(json.dumps({"facts": [], "summary": None})),
    ):
        result = await extract_learnings(agent_config, _history(user_count=2))

    assert result is None
    assert not (mem_dir / "summaries").exists()
