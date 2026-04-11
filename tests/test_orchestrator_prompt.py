"""Tests for orchestrator prompt generation from agents.yaml."""

import pytest
from pathlib import Path
from src.agent.system_prompt import build_orchestrator_prompt


@pytest.fixture
def agents_yaml(tmp_path):
    content = """\
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
"""
    path = tmp_path / "agents.yaml"
    path.write_text(content)
    return path


def test_orchestrator_prompt_contains_agent_table(agents_yaml):
    prompt = build_orchestrator_prompt("Hal", agents_yaml)
    assert "files" in prompt
    assert "system" in prompt
    assert "File operations" in prompt
    assert "Shell commands" in prompt


def test_orchestrator_prompt_contains_name(agents_yaml):
    prompt = build_orchestrator_prompt("Hal", agents_yaml)
    assert "Hal" in prompt


def test_orchestrator_prompt_contains_rules(agents_yaml):
    prompt = build_orchestrator_prompt("Hal", agents_yaml)
    assert "delegate" in prompt.lower()


def test_orchestrator_prompt_missing_agents_file(tmp_path):
    prompt = build_orchestrator_prompt("Hal", tmp_path / "nope.yaml")
    # Should still produce a valid prompt, just with no specialists
    assert "Hal" in prompt
