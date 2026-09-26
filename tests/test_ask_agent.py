"""ask_agent: two-way collaboration inside a container (phase 2)."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.agent import conversation_store
from src.agent.agent import Agent
from src.config import AgentConfig
from src.container import AgentEntry, Container, ContainerManifest
from src.llm import LLMResponse
from src.tools.ask_agent import _ASK_TOOLS, exec_ask_agent
from src.tools.dispatcher import execute_tool_call


def _cfg(tmp_path, name, default):
    ctx = tmp_path / ("" if default else f"agents/{name}")
    (ctx / "memory").mkdir(parents=True, exist_ok=True)
    (ctx / "identity.md").write_text(f"You are {name}.")
    return AgentConfig.for_agent(name, ctx, tmp_path, is_default=default, skill_dirs=[tmp_path / "no-skills"])


@pytest.fixture
def container(tmp_path):
    everyday = Agent(_cfg(tmp_path, "everyday", True))
    finance = Agent(_cfg(tmp_path, "finance", False))
    manifest = ContainerManifest(
        name="home",
        agents=(
            AgentEntry("everyday", "default", ".", True, "generalist"),
            AgentEntry("finance", "finance", "agents/finance", False, "money questions"),
        ),
    )
    return Container(manifest, {"everyday": everyday, "finance": finance})


def _text(content: str) -> LLMResponse:
    return LLMResponse(text=content, tool_calls=[])


def test_ask_agent_schema_only_inside_a_multi_agent_container(container, tmp_path):
    everyday = container.agents["everyday"]
    names = [s["function"]["name"] for s in everyday._get_tool_schemas()]
    assert "ask_agent" in names
    schema = next(s for s in everyday._get_tool_schemas() if s["function"]["name"] == "ask_agent")
    assert schema["function"]["parameters"]["properties"]["agent"]["enum"] == ["finance"]
    assert "finance: money questions" in schema["function"]["description"]
    # a standalone agent never sees it
    solo = Agent(_cfg(tmp_path / "solo", "solo", True))
    assert "ask_agent" not in [s["function"]["name"] for s in solo._get_tool_schemas()]
    # nor does a restricted-tool sub-agent of a container agent
    sub = Agent(everyday.config, tools=list(_ASK_TOOLS))
    sub.container = container
    assert "ask_agent" not in [s["function"]["name"] for s in sub._get_tool_schemas()]


def test_ask_tools_cannot_recurse_or_fan_out():
    assert "ask_agent" not in _ASK_TOOLS and "delegate" not in _ASK_TOOLS


@pytest.mark.asyncio
async def test_ask_agent_returns_the_siblings_answer_and_persists_nothing(container, tmp_path):
    everyday = container.agents["everyday"]
    seen = {}

    async def fake_call_llm(*args, **kwargs):
        messages = kwargs.get("messages") or args[1]  # call_llm(model, messages, tools, ...)
        seen["system"] = messages[0]["content"]
        seen["all"] = "\n".join(str(m.get("content")) for m in messages)
        return _text("Your net worth is 42.")

    with patch("src.agent.agent.call_llm", new=AsyncMock(side_effect=fake_call_llm)):
        out = await exec_ask_agent(
            {"agent": "finance", "question": "What is my net worth?"},
            everyday.config, agent=everyday,
        )
    assert out == "Your net worth is 42."
    # answered with the sibling's identity, framed as a sibling question
    assert "You are finance." in seen["system"]
    assert "[Question from sibling agent 'everyday']" in seen["all"]
    # nothing persisted for either agent, and no live session left behind
    for cfg in (everyday.config, container.agents["finance"].config):
        assert conversation_store.list_conversations(cfg.context_dir) == []
    assert container.agents["finance"].sessions == {}


@pytest.mark.asyncio
async def test_ask_agent_dispatches_through_execute_tool_call(container):
    everyday = container.agents["everyday"]
    with patch("src.agent.agent.call_llm", new=AsyncMock(return_value=_text("ok"))):
        out = await execute_tool_call(
            "ask_agent", {"agent": "finance", "question": "q"}, everyday.config, agent=everyday,
        )
    assert out == "ok"


@pytest.mark.asyncio
async def test_ask_agent_errors(container):
    everyday = container.agents["everyday"]
    cfg = everyday.config
    assert "question" in await exec_ask_agent({"agent": "finance"}, cfg, agent=everyday)
    assert "agent" in await exec_ask_agent({"question": "q"}, cfg, agent=everyday)
    out = await exec_ask_agent({"agent": "ghost", "question": "q"}, cfg, agent=everyday)
    assert "unknown sibling agent 'ghost'" in out and "finance" in out
    out = await exec_ask_agent({"agent": "everyday", "question": "q"}, cfg, agent=everyday)
    assert "unknown sibling" in out
    # no container → refused
    assert "only available inside a multi-agent container" in await exec_ask_agent(
        {"agent": "finance", "question": "q"}, cfg, agent=None,
    )


@pytest.mark.asyncio
async def test_ask_agent_timeout_message(container, monkeypatch):
    everyday = container.agents["everyday"]
    monkeypatch.setattr("src.tools.ask_agent._TIMEOUT", 0.01)

    async def slow(*a, **k):
        await asyncio.sleep(1)
        return _text("late")

    with patch("src.agent.agent.call_llm", new=AsyncMock(side_effect=slow)):
        out = await exec_ask_agent({"agent": "finance", "question": "q"}, everyday.config, agent=everyday)
    assert "time limit" in out
