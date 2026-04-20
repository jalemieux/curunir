# tests/test_run_skill.py
"""Tests for run_skill tool — spawn sub-agent from a skill definition."""

import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path

from src.config import AgentConfig
from src.tools.run_skill import exec_run_skill


def _write_skill(tmp_path: Path, name: str, body: str = "You do things.",
                 tools: list[str] | None = None, max_iterations: int = 5,
                 max_output_tokens: int | None = None) -> Path:
    d = tmp_path / name
    d.mkdir()
    fm = [
        f"name: {name}",
        f'description: "A test skill"',
        f"tools: {tools or ['read']}",
        f"max_iterations: {max_iterations}",
    ]
    if max_output_tokens is not None:
        fm.append(f"max_output_tokens: {max_output_tokens}")
    (d / "SKILL.md").write_text(f"---\n" + "\n".join(fm) + f"\n---\n\n{body}")
    return d


@pytest.fixture
def config_with_skills(tmp_path):
    identity = tmp_path / "identity.md"
    identity.write_text("Test assistant.")
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    return AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        skills_dir=skills_dir,
    )


@pytest.mark.asyncio
async def test_run_skill_spawns_sub_agent(config_with_skills):
    _write_skill(config_with_skills.skills_dir, "research",
                 body="You research things. Do the task.",
                 tools=["read", "grep"], max_iterations=8)

    with patch("src.tools.run_skill.Agent") as MockAgent:
        mock_agent = AsyncMock()
        mock_agent.handle = AsyncMock(return_value="Found the answer.")
        MockAgent.return_value = mock_agent

        result = await exec_run_skill(
            {"skill": "research", "task": "Investigate X", "intent": "one-paragraph summary"},
            config_with_skills,
        )

        assert result == "Found the answer."
        call_kwargs = MockAgent.call_args.kwargs
        assert call_kwargs["tools"] == ["read", "grep"]
        assert "You research things" in call_kwargs["system_prompt_override"]
        sent_message = mock_agent.handle.call_args[0][0]
        assert "Task: Investigate X" in sent_message
        assert "Intent: one-paragraph summary" in sent_message


@pytest.mark.asyncio
async def test_run_skill_applies_iteration_cap(config_with_skills):
    _write_skill(config_with_skills.skills_dir, "quick",
                 tools=["read"], max_iterations=3)

    with patch("src.tools.run_skill.Agent") as MockAgent:
        mock_agent = AsyncMock()
        mock_agent.handle = AsyncMock(return_value="done")
        MockAgent.return_value = mock_agent

        await exec_run_skill(
            {"skill": "quick", "task": "t", "intent": "i"},
            config_with_skills,
        )

        sub_config = MockAgent.call_args.args[0]
        assert sub_config.max_iterations == 3


@pytest.mark.asyncio
async def test_run_skill_truncates_when_over_budget(config_with_skills):
    _write_skill(config_with_skills.skills_dir, "chatty",
                 tools=["read"], max_iterations=5, max_output_tokens=100)

    long_output = "word " * 500  # well over 100 tokens

    with patch("src.tools.run_skill.Agent") as MockAgent:
        mock_agent = AsyncMock()
        mock_agent.handle = AsyncMock(return_value=long_output)
        MockAgent.return_value = mock_agent

        result = await exec_run_skill(
            {"skill": "chatty", "task": "t", "intent": "i"},
            config_with_skills,
        )

        assert "[truncated:" in result
        assert len(result) < len(long_output)


@pytest.mark.asyncio
async def test_run_skill_does_not_truncate_under_budget(config_with_skills):
    _write_skill(config_with_skills.skills_dir, "tight",
                 tools=["read"], max_iterations=5, max_output_tokens=500)

    with patch("src.tools.run_skill.Agent") as MockAgent:
        mock_agent = AsyncMock()
        mock_agent.handle = AsyncMock(return_value="short response")
        MockAgent.return_value = mock_agent

        result = await exec_run_skill(
            {"skill": "tight", "task": "t", "intent": "i"},
            config_with_skills,
        )

        assert result == "short response"
        assert "truncated" not in result


@pytest.mark.asyncio
async def test_run_skill_unknown_skill(config_with_skills):
    result = await exec_run_skill(
        {"skill": "nonexistent", "task": "t", "intent": "i"},
        config_with_skills,
    )
    assert "unknown skill" in result.lower() or "not found" in result.lower()


@pytest.mark.asyncio
async def test_run_skill_requires_intent(config_with_skills):
    _write_skill(config_with_skills.skills_dir, "x", tools=["read"], max_iterations=5)
    result = await exec_run_skill(
        {"skill": "x", "task": "t"},
        config_with_skills,
    )
    assert "intent" in result.lower() and "required" in result.lower()


@pytest.mark.asyncio
async def test_run_skill_requires_task(config_with_skills):
    _write_skill(config_with_skills.skills_dir, "x", tools=["read"], max_iterations=5)
    result = await exec_run_skill(
        {"skill": "x", "intent": "i"},
        config_with_skills,
    )
    assert "task" in result.lower() and "required" in result.lower()


@pytest.mark.asyncio
async def test_run_skill_requires_skill_name(config_with_skills):
    result = await exec_run_skill(
        {"task": "t", "intent": "i"},
        config_with_skills,
    )
    assert "skill" in result.lower() and "required" in result.lower()
