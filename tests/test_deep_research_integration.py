"""Integration test: agent uses delegate() (not inline search) for deep-research."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.agent import Agent, _parse_skill_tools
from src.config import AgentConfig
from src.llm import LLMResponse

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"
DEEP_RESEARCH_SKILL = SKILLS_DIR / "deep-research" / "SKILL.md"


@pytest.fixture
def real_skills_agent_config(tmp_path):
    """AgentConfig pointing at the repository's real skills directory."""
    identity = tmp_path / "identity.md"
    identity.write_text("You are a research assistant.")
    return AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        skills_dir=SKILLS_DIR,
    )


def test_parse_skill_tools_returns_attach_and_delegate():
    """_parse_skill_tools on the updated skill yields both attach and delegate."""
    text = DEEP_RESEARCH_SKILL.read_text()
    tools = _parse_skill_tools(text)
    assert "attach" in tools
    assert "delegate" in tools


class TestDeepResearchDelegationFlow:
    """Simulates an agent running deep-research and verifies it delegates."""

    async def test_agent_calls_delegate_not_web_fetch(self, real_skills_agent_config):
        """When following deep-research, the agent should call delegate per sub-question."""
        agent = Agent(real_skills_agent_config)

        # Scripted LLM responses simulating the deep-research workflow:
        # 1. Load the skill
        # 2. Delegate three sub-questions (one tool call per turn for simplicity)
        # 3. Final synthesis text
        load_skill_call = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "c-load",
                "type": "function",
                "function": {
                    "name": "load_skill",
                    "arguments": json.dumps({"name": "deep-research"}),
                },
            }],
        )
        delegate_call_1 = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "c-d1",
                "type": "function",
                "function": {
                    "name": "delegate",
                    "arguments": json.dumps({
                        "task": (
                            "Research this sub-question: market overview of AI code editors. "
                            "Load these skills first: web-search, reddit-research. "
                            "Return findings in Key Findings + Details + Sources format."
                        ),
                    }),
                },
            }],
        )
        delegate_call_2 = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "c-d2",
                "type": "function",
                "function": {
                    "name": "delegate",
                    "arguments": json.dumps({
                        "task": (
                            "Research this sub-question: developer sentiment on AI code editors. "
                            "Load these skills first: reddit-research, xai-search. "
                            "Return findings in Key Findings + Details + Sources format."
                        ),
                    }),
                },
            }],
        )
        final = LLMResponse(text="Synthesized report.", tool_calls=None)

        responses = [load_skill_call, delegate_call_1, delegate_call_2, final]

        # Custom dispatcher: serve real SKILL.md for load_skill, fake findings for delegate
        async def fake_execute(name, args, config, **kwargs):
            if name == "load_skill":
                return (SKILLS_DIR / args["name"] / "SKILL.md").read_text()
            if name == "delegate":
                return "## Key Findings\n- finding\n## Sources\n- [Web](http://example.com)"
            return f"unexpected tool: {name}"

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=responses), \
             patch("src.agent.agent.execute_tool_call", side_effect=fake_execute) as mock_exec:
            result = await agent.handle("Research AI code editors", "s1")

        assert result == "Synthesized report."

        # Collect all tool calls the agent made
        tool_calls = [call.args[0] for call in mock_exec.call_args_list]
        assert "load_skill" in tool_calls, "agent must load the deep-research skill"
        assert tool_calls.count("delegate") >= 2, (
            f"agent should delegate sub-questions, got tool calls: {tool_calls}"
        )
        # The agent must NOT call inline search/fetch tools — those belong inside sub-agents
        assert "web_fetch" not in tool_calls, (
            f"agent should not run web_fetch inline; sub-agents handle that. Calls: {tool_calls}"
        )

    async def test_skill_load_makes_attach_available(self, real_skills_agent_config):
        """Loading deep-research should add `attach` to the agent's available tools."""
        agent = Agent(real_skills_agent_config)
        session_id = "s-attach"

        load_call = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "c-load",
                "type": "function",
                "function": {
                    "name": "load_skill",
                    "arguments": json.dumps({"name": "deep-research"}),
                },
            }],
        )
        final = LLMResponse(text="ready", tool_calls=None)

        async def fake_execute(name, args, config, **kwargs):
            if name == "load_skill":
                return (SKILLS_DIR / args["name"] / "SKILL.md").read_text()
            return "ok"

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[load_call, final]) as mock_llm, \
             patch("src.agent.agent.execute_tool_call", side_effect=fake_execute):
            await agent.handle("start", session_id)

        # Final call's tool schemas should include attach (added by skill frontmatter)
        # Call args order: model, messages, schemas, ...
        final_schemas = mock_llm.call_args_list[-1].args[2]
        names = [s["function"]["name"] for s in final_schemas]
        assert "attach" in names, f"attach should be available after load_skill, got {names}"
        # Schemas should not duplicate `delegate` (it's a default tool, also declared in skill)
        assert names.count("delegate") == 1, (
            f"delegate must not be duplicated in tool schemas, got {names}"
        )
