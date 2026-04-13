# tests/conftest.py
from pathlib import Path

import pytest

from src.config import AgentConfig


@pytest.fixture
def tmp_context(tmp_path):
    """Create a temporary context directory with a minimal identity file."""
    identity = tmp_path / "identity.md"
    identity.write_text("You are a test assistant.")
    agents = tmp_path / "agents.yaml"
    agents.write_text("files:\n  description: 'File ops'\n  tools: [read]\n  system_prompt: 'Do the task.'\n  max_iterations: 3\n")
    return tmp_path


@pytest.fixture
def tmp_skills(tmp_path):
    """Create a temporary skills directory."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    return skills_dir


@pytest.fixture
def agent_config(tmp_context, tmp_skills):
    """AgentConfig pointing at temporary directories."""
    return AgentConfig(
        identity_file=tmp_context / "identity.md",
        context_dir=tmp_context,
        skills_dir=tmp_skills,
    )
