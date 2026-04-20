# tests/test_orchestrator_prompt.py
"""Orchestrator prompt is identity + skill manifest + delegation principle."""

import pytest
from pathlib import Path

from src.agent.system_prompt import build_orchestrator_prompt
from src.config import AgentConfig


@pytest.fixture
def config_with_skills(tmp_path):
    identity = tmp_path / "identity.md"
    identity.write_text("You are an orchestrator.")
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    for name, desc in [("research", "investigate a topic"), ("reply", "write an email response")]:
        d = skills_dir / name
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\ntools: [read]\nmax_iterations: 5\n---\n"
        )
    return AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        skills_dir=skills_dir,
    )


def test_prompt_includes_identity(config_with_skills):
    prompt = build_orchestrator_prompt(config_with_skills)
    assert "You are an orchestrator." in prompt


def test_prompt_includes_skill_manifest(config_with_skills):
    prompt = build_orchestrator_prompt(config_with_skills)
    assert "research" in prompt
    assert "investigate a topic" in prompt
    assert "reply" in prompt
    assert "write an email response" in prompt


def test_prompt_has_delegation_principle(config_with_skills):
    prompt = build_orchestrator_prompt(config_with_skills)
    assert "run_skill" in prompt


def test_prompt_without_skills(tmp_path):
    identity = tmp_path / "identity.md"
    identity.write_text("You are an orchestrator.")
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    config = AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        skills_dir=skills_dir,
    )
    prompt = build_orchestrator_prompt(config)
    # Identity is still present even with no skills
    assert "You are an orchestrator." in prompt
