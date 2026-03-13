# tests/test_config.py
from pathlib import Path

from src.config import AgentConfig


def test_default_config():
    config = AgentConfig()
    assert config.model == "anthropic/claude-sonnet-4-20250514"
    assert config.max_iterations == 15
    assert config.identity_file == Path("./context/identity.md")
    assert config.skills_dir == Path("./skills")


def test_custom_config():
    config = AgentConfig(model="openai/gpt-4o", max_iterations=5)
    assert config.model == "openai/gpt-4o"
    assert config.max_iterations == 5
