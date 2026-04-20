"""Integration test: orchestrator runs a skill sub-agent and preserves the tool exchange in history."""

import pytest
from unittest.mock import patch

from src.agent.agent import Agent
from src.config import AgentConfig
from src.llm import LLMResponse


@pytest.fixture
def orchestrator(tmp_path):
    identity = tmp_path / "identity.md"
    identity.write_text("# TestBot")
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skill_dir = skills_dir / "hostname-check"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: hostname-check\n"
        'description: "Read a system file and report a value"\n'
        "tools: [read]\n"
        "max_iterations: 3\n"
        "---\n\n"
        "You are a file specialist. Do the task and report the result."
    )

    from src.agent.system_prompt import build_orchestrator_prompt

    config = AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        skills_dir=skills_dir,
    )
    prompt = build_orchestrator_prompt(config)
    return Agent(
        config,
        tools=["read", "edit", "write", "bash", "grep", "glob", "web_fetch", "schedule", "run_skill"],
        system_prompt_override=prompt,
    )


@pytest.mark.asyncio
async def test_orchestrator_runs_skill_and_preserves_tool_exchange(orchestrator):
    """Full flow: user asks -> orchestrator calls run_skill -> sub-agent replies -> final answer."""
    orchestrator_responses = [
        LLMResponse(
            text=None,
            tool_calls=[{
                "id": "tc1",
                "type": "function",
                "function": {
                    "name": "run_skill",
                    "arguments": (
                        '{"skill": "hostname-check", '
                        '"task": "Read /etc/hostname", '
                        '"intent": "report the hostname value"}'
                    ),
                },
            }],
        ),
        LLMResponse(text="The hostname is 'devbox'.", tool_calls=None),
    ]
    sub_agent_responses = [
        LLMResponse(text="The file contains: devbox", tool_calls=None),
    ]

    call_count = 0

    async def mock_call_llm(model, messages, tools, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return orchestrator_responses[0]
        elif call_count == 2:
            return sub_agent_responses[0]
        else:
            return orchestrator_responses[1]

    with patch("src.agent.agent.call_llm", side_effect=mock_call_llm):
        result = await orchestrator.handle("what is the hostname?", "sess1")

    assert result == "The hostname is 'devbox'."

    history = orchestrator.sessions["sess1"]
    roles = [m["role"] for m in history]
    assert "tool" in roles
    assert any(m["role"] == "assistant" and "tool_calls" in m for m in history)
