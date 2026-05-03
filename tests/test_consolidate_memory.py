# tests/test_consolidate_memory.py
import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.config import AgentConfig
from src.llm import LLMResponse


def _load_module():
    """Import scripts/consolidate_memory.py without requiring it to be a package."""
    import sys
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "scripts" / "consolidate_memory.py"
    spec = importlib.util.spec_from_file_location("consolidate_memory", script_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["consolidate_memory"] = module
    spec.loader.exec_module(module)
    return module


cm = _load_module()


def test_consolidate_dedupes_sections(tmp_path):
    target = tmp_path / "tasks.md"
    target.write_text(
        "## Adobe Agentic Role\n**Source:** chat - 2026-04-01\n**Fact:** v1\n\n"
        "## Adobe Agentic Role\n**Source:** chat - 2026-04-15\n**Fact:** v2\n\n"
        "## Adobe Agentic Role\n**Source:** chat - 2026-05-01\n**Fact:** v3\n"
    )

    consolidated = (
        "## Adobe Agentic Role\n"
        "**Source:** chat - 2026-04-01, 2026-04-15, 2026-05-01\n"
        "**Fact:** v3\n"
    )
    llm_response = LLMResponse(text=consolidated, tool_calls=None)
    config = AgentConfig()

    with patch("consolidate_memory.call_llm", new_callable=AsyncMock, return_value=llm_response):
        asyncio.run(cm.consolidate(target, config, dry_run=False))

    text = target.read_text()
    assert text.count("## Adobe Agentic Role") == 1
    assert "v3" in text
    assert "v1" not in text
    assert "v2" not in text


def test_consolidate_dry_run_does_not_mutate(tmp_path):
    target = tmp_path / "tasks.md"
    original = "## A\n**Fact:** original\n\n## A\n**Fact:** dup\n"
    target.write_text(original)

    llm_response = LLMResponse(text="## A\n**Fact:** consolidated\n", tool_calls=None)
    config = AgentConfig()

    with patch("consolidate_memory.call_llm", new_callable=AsyncMock, return_value=llm_response):
        result = asyncio.run(cm.consolidate(target, config, dry_run=True))

    assert target.read_text() == original
    assert "consolidated" in result


def test_consolidate_strips_code_fences(tmp_path):
    target = tmp_path / "tasks.md"
    target.write_text("## A\noriginal\n")

    llm_response = LLMResponse(
        text="```markdown\n## A\nclean\n```",
        tool_calls=None,
    )
    config = AgentConfig()

    with patch("consolidate_memory.call_llm", new_callable=AsyncMock, return_value=llm_response):
        asyncio.run(cm.consolidate(target, config, dry_run=False))

    text = target.read_text()
    assert "```" not in text
    assert "## A" in text
    assert "clean" in text


def test_consolidate_handles_real_world_pattern(tmp_path):
    """Adobe x6, X Listener x3 — the duplication pattern from issue #35."""
    target = tmp_path / "tasks.md"
    target.write_text(
        "## Adobe Agentic Role\n**Fact:** entry 1\n\n"
        "## Adobe Agentic Role\n**Fact:** entry 2\n\n"
        "## Adobe Agentic Role\n**Fact:** entry 3\n\n"
        "## Adobe Agentic Role\n**Fact:** entry 4\n\n"
        "## Adobe Agentic Role\n**Fact:** entry 5\n\n"
        "## Adobe Agentic Role\n**Fact:** entry 6 latest\n\n"
        "## X Listener\n**Fact:** x1\n\n"
        "## X Listener\n**Fact:** x2\n\n"
        "## X Listener\n**Fact:** x3 latest\n"
    )

    consolidated = (
        "## Adobe Agentic Role\n**Fact:** entry 6 latest\n\n"
        "## X Listener\n**Fact:** x3 latest\n"
    )
    llm_response = LLMResponse(text=consolidated, tool_calls=None)
    config = AgentConfig()

    with patch("consolidate_memory.call_llm", new_callable=AsyncMock, return_value=llm_response):
        asyncio.run(cm.consolidate(target, config, dry_run=False))

    text = target.read_text()
    assert text.count("## Adobe Agentic Role") == 1
    assert text.count("## X Listener") == 1
    assert "entry 6 latest" in text
    assert "x3 latest" in text
    assert "entry 1" not in text
