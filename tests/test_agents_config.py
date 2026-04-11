"""Tests for src/agent/agents_config.py — agents.yaml loading."""

import pytest
from pathlib import Path
from src.agent.agents_config import SubAgentDef, load_agents_config


@pytest.fixture
def agents_yaml(tmp_path):
    """Write a minimal agents.yaml and return its path."""
    content = """\
files:
  description: "File operations"
  tools: [glob, grep, read, edit, write]
  system_prompt: >
    You are a file operations specialist. Complete the task below.
    Report what you did in under 100 words.
  max_iterations: 10

system:
  description: "Shell commands"
  tools: [bash]
  system_prompt: >
    You are a system operations specialist. Run commands to complete the task.
    Report output concisely.
  max_iterations: 10
"""
    path = tmp_path / "agents.yaml"
    path.write_text(content)
    return path


def test_load_returns_dict_of_sub_agent_defs(agents_yaml):
    agents = load_agents_config(agents_yaml)
    assert isinstance(agents, dict)
    assert "files" in agents
    assert "system" in agents
    assert isinstance(agents["files"], SubAgentDef)


def test_sub_agent_def_fields(agents_yaml):
    agents = load_agents_config(agents_yaml)
    files = agents["files"]
    assert files.name == "files"
    assert files.description == "File operations"
    assert files.tools == ["glob", "grep", "read", "edit", "write"]
    assert "file operations specialist" in files.system_prompt.lower()
    assert files.max_iterations == 10


def test_missing_file_returns_empty_dict(tmp_path):
    agents = load_agents_config(tmp_path / "nonexistent.yaml")
    assert agents == {}


def test_agent_names_method(agents_yaml):
    agents = load_agents_config(agents_yaml)
    names = sorted(agents.keys())
    assert names == ["files", "system"]
