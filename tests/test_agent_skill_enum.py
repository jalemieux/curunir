# tests/test_agent_skill_enum.py
"""Agent injects skill names into run_skill schema enum at construction."""

import pytest
from src.agent.agent import Agent
from src.config import AgentConfig


@pytest.fixture
def config_with_skills(tmp_path):
    identity = tmp_path / "identity.md"
    identity.write_text("Test.")
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    for name in ["alpha", "beta"]:
        d = skills_dir / name
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\ntools: [read]\nmax_iterations: 5\n---\n"
        )
    return AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        skills_dir=skills_dir,
    )


def test_run_skill_enum_populated(config_with_skills):
    agent = Agent(config_with_skills, tools=["run_skill"])
    schemas = agent._get_tool_schemas(session_id="s1")
    run_skill_schema = next(s for s in schemas if s["function"]["name"] == "run_skill")
    enum = run_skill_schema["function"]["parameters"]["properties"]["skill"].get("enum")
    assert enum == ["alpha", "beta"]


def test_no_enum_when_no_skills(tmp_path):
    identity = tmp_path / "identity.md"
    identity.write_text("Test.")
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    config = AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        skills_dir=skills_dir,
    )
    agent = Agent(config, tools=["run_skill"])
    schemas = agent._get_tool_schemas(session_id="s1")
    run_skill_schema = next(s for s in schemas if s["function"]["name"] == "run_skill")
    assert "enum" not in run_skill_schema["function"]["parameters"]["properties"]["skill"]
