"""Integration check: gate rewrites into a directive that points at a
real skill, and the four onboarding skills are discoverable from the
repo's actual skills/ tree."""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.agent import Agent
from src.config import AgentConfig
from src.llm import LLMResponse
from src.skills import load_registry


REPO_SKILLS = Path(__file__).resolve().parent.parent / "skills"


class TestOnboardingSkillsExist:
    def test_orchestrator_registers(self):
        registry = load_registry([REPO_SKILLS])
        assert "onboarding" in registry

    def test_three_sub_skills_register(self):
        registry = load_registry([REPO_SKILLS])
        for name in ("profile", "preferences", "personality"):
            assert name in registry, f"missing sub-skill: {name}"


class TestGateMessageMatchesOrchestrator:
    async def test_gate_directive_names_the_onboarding_skill(self, tmp_path):
        """The text the gate injects must reference the `onboarding` skill by
        name, otherwise the LLM has no way to bridge gate → orchestrator."""
        identity = tmp_path / "identity.md"
        identity.write_text("You are a test assistant.")
        config = AgentConfig(
            identity_file=identity,
            context_dir=tmp_path,
            skill_dirs=[REPO_SKILLS],
        )
        agent = Agent(config)
        mock_response = LLMResponse(text="welcome", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await agent.handle("hi", "s1")
        user_msg = next(m for m in mock_llm.call_args[0][1] if m["role"] == "user")
        assert "onboarding" in user_msg["content"].lower()

        registry = load_registry([REPO_SKILLS])
        assert "onboarding" in registry, (
            "gate references the 'onboarding' skill but no skill of that name is registered"
        )
