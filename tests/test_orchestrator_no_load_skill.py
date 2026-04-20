# tests/test_orchestrator_no_load_skill.py
"""Small-model orchestrator does not expose load_skill."""

import pytest
from pathlib import Path

from src.agent.agent import Agent
from src.config import AgentConfig


def test_orchestrator_tools_exclude_load_skill(tmp_path):
    identity = tmp_path / "identity.md"
    identity.write_text("Orchestrator.")
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    config = AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        skills_dir=skills_dir,
    )
    agent = Agent(
        config,
        tools=["read", "edit", "write", "bash", "grep", "glob", "web_fetch", "schedule", "run_skill"],
        system_prompt_override="orchestrator",
    )
    names = {s["function"]["name"] for s in agent._get_tool_schemas("s1")}
    assert "load_skill" not in names
    assert "delegate" not in names
    assert "run_skill" in names
