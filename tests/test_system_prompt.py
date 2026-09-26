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
    # The load-by-name signpost survives assembly into the system prompt (#451).
    assert "load_skill" in result


def test_no_skills_section_when_empty(tmp_context, tmp_skills, agent_config):
    result = build_static_prompt(agent_config)
    assert "Available Skills" not in result


def test_missing_identity_file_warns_and_builds(tmp_path, tmp_skills, caplog, monkeypatch):
    """Identity is the personality layer only — missing it warns rather than
    failing, and the prompt still builds from the remaining layers."""
    monkeypatch.setattr("src.persona.PERSONAS_DIR", tmp_path / "personas")
    config = AgentConfig(
        identity_file=tmp_path / "nonexistent.md",
        persona="no-such-persona",
        skill_dirs=[tmp_skills],
    )
    with caplog.at_level("WARNING"):
        result = build_static_prompt(config)
    assert "Identity file not found" in caplog.text
    assert result == ""


def test_persona_prompts_appended_sorted(tmp_context, tmp_skills, agent_config, monkeypatch, tmp_path):
    personas_root = tmp_path / "personas"
    prompts = personas_root / "demo" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "20-guardrails.md").write_text("GUARDRAILS BLOCK")
    (prompts / "10-domain.md").write_text("DOMAIN BLOCK")
    monkeypatch.setattr("src.persona.PERSONAS_DIR", personas_root)
    agent_config.persona = "demo"

    result = build_static_prompt(agent_config)

    assert "You are a test assistant." in result
    # sorted by filename: 10-domain before 20-guardrails
    assert result.index("DOMAIN BLOCK") < result.index("GUARDRAILS BLOCK")


def test_missing_persona_prompts_dir_is_silently_skipped(tmp_context, tmp_skills, agent_config, monkeypatch, tmp_path):
    monkeypatch.setattr("src.persona.PERSONAS_DIR", tmp_path / "personas")
    agent_config.persona = "no-such-persona"
    result = build_static_prompt(agent_config)
    assert "You are a test assistant." in result


def test_skill_allowlist_forwarded_to_manifest(tmp_context, tmp_skills, agent_config):
    for n in ("identity", "comfyui"):
        d = tmp_skills / n
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {n}\ndescription: does {n}\n---\nbody\n"
        )
    agent_config.skill_allowlist = ["identity"]
    result = build_static_prompt(agent_config)
    assert "identity" in result
    assert "comfyui" not in result


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

        captured: list[str] = []

        async def fake_call_llm(model, messages, tools, **kw):
            captured.append(messages[0]["content"])
            return LLMResponse(text="ok", tool_calls=None)

        with patch("src.agent.agent.call_llm", new=fake_call_llm):
            result = await agent.handle("hi", "s1")

        assert result == "ok"


def test_persona_prompts_appended_after_identity(tmp_context, tmp_skills, agent_config, monkeypatch, tmp_path):
    personas_root = tmp_path / "personas"
    prompts = personas_root / "demo" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "behavior.md").write_text("BEHAVIOR LAYER")
    monkeypatch.setattr("src.persona.PERSONAS_DIR", personas_root)
    agent_config.persona = "demo"
    result = build_static_prompt(agent_config)
    assert result.index("You are a test assistant.") < result.index("BEHAVIOR LAYER")


# --- {{context}} / {{shared}} rendering over the static prefix --------------

def test_static_prompt_renders_placeholders_in_identity(tmp_path, tmp_skills):
    ctx = tmp_path / "agents" / "fin"
    ctx.mkdir(parents=True)
    (ctx / "identity.md").write_text(
        "Notes live in {{context}}/memory/, deliverables in {{shared}}/workspace/."
    )
    config = AgentConfig.for_agent("fin", ctx, tmp_path, skill_dirs=[tmp_skills])
    prompt = build_static_prompt(config)
    assert f"{ctx}/memory/" in prompt
    assert f"{tmp_path}/workspace/" in prompt
    assert "{{" not in prompt


def test_static_prompt_renders_placeholders_in_persona_prompts_and_manifest(
    tmp_path, tmp_skills, monkeypatch,
):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "identity.md").write_text("id")
    personas = tmp_path / "personas" / "p" / "prompts"
    personas.mkdir(parents=True)
    (personas / "10-x.md").write_text("Save skills under {{context}}/skills/.")
    monkeypatch.setattr("src.persona.PERSONAS_DIR", tmp_path / "personas")
    skill = tmp_skills / "s"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: s\ndescription: writes to {{shared}}/workspace/generated\n---\nbody\n"
    )
    config = AgentConfig.for_agent(
        "a", ctx, tmp_path / "shared", persona="p", skill_dirs=[tmp_skills],
    )
    prompt = build_static_prompt(config)
    assert f"{ctx}/skills/" in prompt
    assert f"{tmp_path / 'shared'}/workspace/generated" in prompt
    assert "{{" not in prompt


def test_legacy_static_prompt_renders_placeholders_to_context(tmp_context, tmp_skills):
    """The legacy layout renders both placeholders to the historical literal."""
    (tmp_context / "identity.md").write_text("{{context}}/memory and {{shared}}/workspace")
    config = AgentConfig(
        identity_file=tmp_context / "identity.md",
        context_dir=Path("./context"),
        skill_dirs=[tmp_skills],
    )
    assert "context/memory and context/workspace" in build_static_prompt(config)
