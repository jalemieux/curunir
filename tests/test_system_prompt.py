# tests/test_system_prompt.py
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.agent import Agent
from src.agent.system_prompt import build_memory_block, build_static_prompt
from src.config import AgentConfig
from src.llm import LLMResponse


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
        skill_dirs=[tmp_skills],
    )
    with pytest.raises(FileNotFoundError, match="identity file"):
        build_static_prompt(config)


def test_includes_behavior_file_when_present(tmp_context, tmp_skills, agent_config):
    behavior = tmp_context / "behavior.md"
    behavior.write_text("## Guidelines\n- be concise")
    agent_config.behavior_file = behavior

    result = build_static_prompt(agent_config)

    assert "You are a test assistant." in result
    assert "be concise" in result


def test_missing_behavior_file_is_silently_skipped(tmp_context, tmp_skills, agent_config):
    agent_config.behavior_file = tmp_context / "does-not-exist.md"

    result = build_static_prompt(agent_config)

    assert "You are a test assistant." in result


class TestBuildMemoryBlock:
    def test_coalesces_readme_and_profile(self, tmp_context):
        memory = tmp_context / "memory"
        memory.mkdir()
        (memory / "README.md").write_text("# Routing map\nWhere to look first.")
        (memory / "profile.md").write_text("# Owner Profile\nName: Alice")

        block = build_memory_block(tmp_context)

        assert "Where to look first." in block
        assert "Name: Alice" in block

    def test_empty_when_memory_dir_missing(self, tmp_context):
        block = build_memory_block(tmp_context)
        assert block == ""

    def test_skips_missing_readme(self, tmp_context):
        memory = tmp_context / "memory"
        memory.mkdir()
        (memory / "profile.md").write_text("# Owner Profile\nName: Alice")

        block = build_memory_block(tmp_context)

        assert "Routing map" not in block
        assert "Name: Alice" in block

    def test_skips_missing_profile(self, tmp_context):
        memory = tmp_context / "memory"
        memory.mkdir()
        (memory / "README.md").write_text("# Routing map")

        block = build_memory_block(tmp_context)

        assert "Routing map" in block
        assert "Owner Profile" not in block


class TestSessionMemorySnapshot:
    """Memory block is read once per session and reused across turns."""

    async def test_first_turn_inlines_memory_into_system_prompt(self, agent_config):
        memory = agent_config.context_dir / "memory"
        memory.mkdir()
        (memory / "README.md").write_text("# Routing map\nLook at people/")
        (memory / "profile.md").write_text("# Owner Profile\nName: Alice")

        agent = Agent(agent_config)
        (agent_config.context_dir / ".onboarded").touch()

        captured: list[list[dict]] = []

        async def fake_call_llm(model, messages, tools, **kw):
            captured.append(list(messages))
            return LLMResponse(text="ok", tool_calls=None)

        with patch("src.agent.agent.call_llm", new=fake_call_llm):
            await agent.handle("hi", "s1")

        system_msg = captured[0][0]["content"]
        assert "Look at people/" in system_msg
        assert "Name: Alice" in system_msg

    async def test_snapshot_reused_across_turns_in_same_session(self, agent_config):
        memory = agent_config.context_dir / "memory"
        memory.mkdir()
        readme = memory / "README.md"
        readme.write_text("# Routing map\nVersion ONE")
        profile = memory / "profile.md"
        profile.write_text("# Owner Profile\nFirst snapshot")

        agent = Agent(agent_config)
        (agent_config.context_dir / ".onboarded").touch()

        captured: list[str] = []

        async def fake_call_llm(model, messages, tools, **kw):
            captured.append(messages[0]["content"])
            return LLMResponse(text="ok", tool_calls=None)

        with patch("src.agent.agent.call_llm", new=fake_call_llm):
            await agent.handle("first", "s1")
            # Mutate the files on disk — the cached snapshot must ignore this.
            readme.write_text("# Routing map\nVersion TWO")
            profile.write_text("# Owner Profile\nSecond snapshot")
            await agent.handle("second", "s1")

        # Both turns saw the same memory snapshot.
        assert "Version ONE" in captured[0]
        assert "First snapshot" in captured[0]
        assert captured[0] == captured[1]
        assert "Version TWO" not in captured[1]

    async def test_new_session_triggers_fresh_read(self, agent_config):
        memory = agent_config.context_dir / "memory"
        memory.mkdir()
        readme = memory / "README.md"
        readme.write_text("# Routing map\nVersion ONE")
        profile = memory / "profile.md"
        profile.write_text("# Owner Profile\nFirst snapshot")

        agent = Agent(agent_config)
        (agent_config.context_dir / ".onboarded").touch()

        captured: list[str] = []

        async def fake_call_llm(model, messages, tools, **kw):
            captured.append(messages[0]["content"])
            return LLMResponse(text="ok", tool_calls=None)

        with patch("src.agent.agent.call_llm", new=fake_call_llm):
            await agent.handle("first", "s1")
            readme.write_text("# Routing map\nVersion TWO")
            profile.write_text("# Owner Profile\nSecond snapshot")
            await agent.handle("second", "s2")

        assert "Version ONE" in captured[0]
        assert "Version TWO" in captured[1]
        assert "Second snapshot" in captured[1]

    async def test_missing_memory_files_handled_gracefully(self, agent_config):
        """First turn with no memory dir must still call the LLM."""
        agent = Agent(agent_config)
        (agent_config.context_dir / ".onboarded").touch()

        captured: list[str] = []

        async def fake_call_llm(model, messages, tools, **kw):
            captured.append(messages[0]["content"])
            return LLMResponse(text="ok", tool_calls=None)

        with patch("src.agent.agent.call_llm", new=fake_call_llm):
            result = await agent.handle("hi", "s1")

        assert result == "ok"
