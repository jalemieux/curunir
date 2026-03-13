# tests/test_system_prompt.py
from pathlib import Path

import pytest

from src.agent.system_prompt import build_static_prompt
from src.config import AgentConfig


def test_builds_prompt_with_identity(tmp_context, tmp_skills, agent_config):
    result = build_static_prompt(agent_config)
    assert "You are a test assistant." in result


def test_includes_skill_manifest(tmp_context, tmp_skills, agent_config):
    skill_dir = tmp_skills / "research"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: research\ndescription: When investigating\n---\n"
    )
    result = build_static_prompt(agent_config)
    assert "research" in result
    assert "Available Skills" in result


def test_no_skills_section_when_empty(tmp_context, tmp_skills, agent_config):
    result = build_static_prompt(agent_config)
    assert "Available Skills" not in result


def test_missing_identity_file_raises(tmp_path, tmp_skills):
    config = AgentConfig(
        identity_file=tmp_path / "nonexistent.md",
        skills_dir=tmp_skills,
    )
    with pytest.raises(FileNotFoundError, match="identity file"):
        build_static_prompt(config)
