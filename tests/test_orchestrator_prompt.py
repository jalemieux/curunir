"""Tests for orchestrator prompt: identity.md + specialist table from agents.yaml."""

import pytest
from src.agent.system_prompt import build_orchestrator_prompt
from src.config import AgentConfig


@pytest.fixture
def config_with_agents(tmp_path):
    identity = tmp_path / "identity.md"
    identity.write_text("You are curunir, a proactive assistant.\n\n## Guidelines\n- Be concise.\n")
    agents_file = tmp_path / "agents.yaml"
    agents_file.write_text("""\
files:
  description: "File operations — read, edit, write, search"
  tools: [glob, grep, read, edit, write]
  system_prompt: "Do file stuff."
  max_iterations: 10

system:
  description: "Shell commands and system management"
  tools: [bash]
  system_prompt: "Do system stuff."
  max_iterations: 10
""")
    return AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        agents_file=agents_file,
    )


def test_orchestrator_prompt_contains_identity(config_with_agents):
    prompt = build_orchestrator_prompt(config_with_agents)
    assert "curunir" in prompt
    assert "Be concise" in prompt


def test_orchestrator_prompt_contains_agent_table(config_with_agents):
    prompt = build_orchestrator_prompt(config_with_agents)
    assert "files" in prompt
    assert "system" in prompt
    assert "File operations" in prompt
    assert "Shell commands" in prompt


def test_orchestrator_prompt_mentions_delegation(config_with_agents):
    prompt = build_orchestrator_prompt(config_with_agents)
    assert "delegate" in prompt.lower()


def test_orchestrator_prompt_missing_agents_file(tmp_path):
    identity = tmp_path / "identity.md"
    identity.write_text("You are curunir.")
    config = AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        agents_file=tmp_path / "nope.yaml",
    )
    prompt = build_orchestrator_prompt(config)
    # Should still produce a valid prompt from identity alone
    assert "curunir" in prompt


def test_orchestrator_prompt_missing_identity_raises(tmp_path):
    config = AgentConfig(
        identity_file=tmp_path / "missing.md",
        context_dir=tmp_path,
        agents_file=tmp_path / "agents.yaml",
    )
    with pytest.raises(FileNotFoundError):
        build_orchestrator_prompt(config)
