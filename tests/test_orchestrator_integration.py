"""Integration test: orchestrator delegates to a named sub-agent and compacts history."""

import pytest
from unittest.mock import AsyncMock, patch

from src.agent.agent import Agent
from src.config import AgentConfig
from src.llm import LLMResponse


_AGENTS_YAML = """\
files:
  description: "File operations"
  tools: [read]
  system_prompt: "You are a file specialist."
  max_iterations: 3
"""


@pytest.fixture
def orchestrator(tmp_path):
    identity = tmp_path / "identity.md"
    identity.write_text("# TestBot")
    agents_file = tmp_path / "agents.yaml"
    agents_file.write_text(_AGENTS_YAML)

    from src.agent.system_prompt import build_orchestrator_prompt

    config = AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        agents_file=agents_file,
        max_history_chars=16_000,
    )
    prompt = build_orchestrator_prompt(config)
    return Agent(config, tools=["delegate"], system_prompt_override=prompt)


@pytest.mark.asyncio
async def test_orchestrator_delegates_and_compacts(orchestrator):
    """Full flow: user asks -> orchestrator delegates -> result compacted -> final answer."""
    # The orchestrator will make two LLM calls:
    # 1. Decides to delegate to files agent
    # 2. After getting the result, responds to user
    orchestrator_responses = [
        LLMResponse(
            text=None,
            tool_calls=[{
                "id": "tc1",
                "type": "function",
                "function": {
                    "name": "delegate",
                    "arguments": '{"agent": "files", "task": "Read /etc/hostname"}',
                },
            }],
        ),
        LLMResponse(text="The hostname is 'devbox'.", tool_calls=None),
    ]

    # The sub-agent spawned by delegate will also call the LLM
    sub_agent_responses = [
        LLMResponse(text="The file contains: devbox", tool_calls=None),
    ]

    call_count = 0

    async def mock_call_llm(model, messages, tools, **kwargs):
        nonlocal call_count
        call_count += 1
        # Call order: orchestrator(1) -> sub-agent(2) -> orchestrator(3)
        if call_count == 1:
            return orchestrator_responses[0]
        elif call_count == 2:
            return sub_agent_responses[0]
        else:
            return orchestrator_responses[1]

    with patch("src.agent.agent.call_llm", side_effect=mock_call_llm):
        result = await orchestrator.handle("what is the hostname?", "sess1")

    assert result == "The hostname is 'devbox'."

    # History should be compacted: no raw tool messages
    history = orchestrator.sessions["sess1"]
    roles = [m["role"] for m in history]
    assert "tool" not in roles
