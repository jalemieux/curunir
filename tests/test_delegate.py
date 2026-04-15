# tests/test_delegate.py
"""Tests for delegate tool — named agent delegation with result truncation."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from src.config import AgentConfig
from src.tools.delegate import exec_delegate

_AGENTS_YAML = """\
files:
  description: "File operations"
  tools: [glob, grep, read]
  system_prompt: "You are a file specialist. Do the task."
  max_iterations: 5

system:
  description: "Shell commands"
  tools: [bash]
  system_prompt: "You are a system specialist. Do the task."
  max_iterations: 3
"""


@pytest.fixture
def config_with_agents(tmp_path):
    identity = tmp_path / "identity.md"
    identity.write_text("Test assistant.")
    agents_file = tmp_path / "agents.yaml"
    agents_file.write_text(_AGENTS_YAML)
    return AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        agents_file=agents_file,
    )


@pytest.mark.asyncio
async def test_delegate_to_named_agent(config_with_agents):
    with patch("src.tools.delegate.Agent") as MockAgent:
        mock_agent = AsyncMock()
        mock_agent.handle = AsyncMock(return_value="Found 3 files.")
        MockAgent.return_value = mock_agent

        result = await exec_delegate(
            {"agent": "files", "task": "List all .py files", "intent": "count of matches"},
            config_with_agents,
        )

        assert result == "Found 3 files."
        # Verify sub-agent was created with the right tools
        call_kwargs = MockAgent.call_args
        assert call_kwargs[1]["tools"] == ["glob", "grep", "read"]
        # Verify the sub-agent received both task and intent
        sent_message = mock_agent.handle.call_args[0][0]
        assert "Task: List all .py files" in sent_message
        assert "Intent: count of matches" in sent_message


@pytest.mark.asyncio
async def test_delegate_requires_intent(config_with_agents):
    result = await exec_delegate(
        {"agent": "files", "task": "read foo.txt"},
        config_with_agents,
    )
    assert "intent" in result.lower() and "required" in result.lower()


@pytest.mark.asyncio
async def test_delegate_unknown_agent(config_with_agents):
    result = await exec_delegate(
        {"agent": "nonexistent", "task": "do something", "intent": "report status"},
        config_with_agents,
    )
    assert "unknown agent" in result.lower()


@pytest.mark.asyncio
async def test_delegate_no_agents_file(tmp_path):
    identity = tmp_path / "identity.md"
    identity.write_text("Test assistant.")
    config = AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        agents_file=tmp_path / "nonexistent.yaml",
    )
    result = await exec_delegate(
        {"agent": "files", "task": "do something", "intent": "report status"},
        config,
    )
    assert "no agents" in result.lower() or "not configured" in result.lower()


@pytest.mark.asyncio
async def test_delegate_truncates_long_result(config_with_agents):
    long_result = "x" * 5000
    with patch("src.tools.delegate.Agent") as MockAgent:
        mock_agent = AsyncMock()
        mock_agent.handle = AsyncMock(return_value=long_result)
        MockAgent.return_value = mock_agent

        result = await exec_delegate(
            {"agent": "files", "task": "read a huge file", "intent": "summarize"},
            config_with_agents,
        )

        assert len(result) <= 2048 + 50  # ~500 tokens ≈ 2000 chars, with margin
        assert result.endswith("... (truncated)")


@pytest.mark.asyncio
async def test_delegate_uses_agent_max_iterations(config_with_agents):
    with patch("src.tools.delegate.Agent") as MockAgent:
        mock_agent = AsyncMock()
        mock_agent.handle = AsyncMock(return_value="done")
        MockAgent.return_value = mock_agent

        await exec_delegate(
            {"agent": "system", "task": "run uptime", "intent": "report uptime value"},
            config_with_agents,
        )

        call_kwargs = MockAgent.call_args
        config_arg = call_kwargs[0][0]  # first positional arg is config
        assert config_arg.max_iterations == 3


@pytest.mark.asyncio
async def test_delegate_uses_agent_system_prompt(config_with_agents):
    with patch("src.tools.delegate.Agent") as MockAgent:
        mock_agent = AsyncMock()
        mock_agent.handle = AsyncMock(return_value="done")
        MockAgent.return_value = mock_agent

        await exec_delegate(
            {"agent": "system", "task": "run uptime", "intent": "report uptime value"},
            config_with_agents,
        )

        call_kwargs = MockAgent.call_args
        assert call_kwargs[1]["system_prompt_override"] == "You are a system specialist. Do the task."
