# tests/test_agent.py
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.agent import Agent
from src.llm import LLMResponse


@pytest.fixture
def agent(agent_config):
    return Agent(agent_config)


class TestAgentHandle:
    async def test_returns_text_response(self, agent):
        mock_response = LLMResponse(text="Hello!", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await agent.handle("hi", "test-session")
        assert result == "Hello!"

    async def test_session_persistence(self, agent):
        response1 = LLMResponse(text="First", tool_calls=None)
        response2 = LLMResponse(text="Second", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[response1, response2]):
            await agent.handle("msg1", "s1")
            await agent.handle("msg2", "s1")
        history = agent.sessions["s1"]
        assert len(history) == 4  # user, assistant, user, assistant
        assert history[0]["content"] == "msg1"
        assert history[1]["content"] == "First"

    async def test_separate_sessions(self, agent):
        mock_response = LLMResponse(text="Reply", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response):
            await agent.handle("msg1", "session-a")
            await agent.handle("msg2", "session-b")
        assert len(agent.sessions["session-a"]) == 2
        assert len(agent.sessions["session-b"]) == 2

    async def test_executes_tool_calls(self, agent):
        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "echo tool_test"})},
            }],
        )
        text_response = LLMResponse(text="Done!", tool_calls=None)

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[tool_response, text_response]):
            result = await agent.handle("run something", "s1")

        assert result == "Done!"
        history = agent.sessions["s1"]
        # user, assistant(tool_calls), tool, assistant(text)
        assert len(history) == 4
        assert history[1].get("tool_calls") is not None
        assert history[2]["role"] == "tool"
        assert "tool_test" in history[2]["content"]

    async def test_handles_combined_text_and_tool_calls(self, agent):
        combined = LLMResponse(
            text="Let me check",
            tool_calls=[{
                "id": "call_2",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "echo combined"})},
            }],
        )
        final = LLMResponse(text="All done", tool_calls=None)

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[combined, final]):
            result = await agent.handle("check", "s1")

        assert result == "All done"
        # Content is dropped when tool_calls are present: some thinking-mode
        # providers (GLM via DeepInfra) reject an assistant message carrying
        # both content and tool_calls as an incompatible "prefill".
        assert "content" not in agent.sessions["s1"][1]
        assert agent.sessions["s1"][1].get("tool_calls") is not None

    async def test_empty_response_returns_error(self, agent):
        empty = LLMResponse(text=None, tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=empty):
            result = await agent.handle("hello", "s1")
        assert "error" in result.lower()

    async def test_max_iterations(self, agent_config):
        agent_config.max_iterations = 2
        agent = Agent(agent_config)

        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_loop",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "echo loop"})},
            }],
        )
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=tool_response):
            result = await agent.handle("loop forever", "s1")
        assert "iteration limit" in result.lower()


class TestDelegateToolExecution:
    async def test_delegate_via_agent_handle(self, agent):
        """Delegate tool calls go through the unified execute_tool_call."""
        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_delegate",
                "type": "function",
                "function": {"name": "delegate", "arguments": json.dumps({"agent": "files", "task": "say hello", "intent": "confirm greeting"})},
            }],
        )
        text_response = LLMResponse(text="Done", tool_calls=None)

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[tool_response, text_response]), \
             patch("src.agent.agent.execute_tool_call", new_callable=AsyncMock, return_value="sub-agent result"):
            result = await agent.handle("delegate this", "s1")

        assert result == "Done"
        # Verify the delegate exchange is preserved as a proper tool_call + tool_result pair
        history = agent.sessions["s1"]
        tool_msgs = [m for m in history if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["content"] == "sub-agent result"
        assert tool_msgs[0]["tool_call_id"] == "call_delegate"
        assistant_with_calls = [m for m in history if m["role"] == "assistant" and "tool_calls" in m]
        assert len(assistant_with_calls) == 1


class TestToolAllowlist:
    async def test_only_allowed_tools_in_schemas(self, agent_config):
        agent = Agent(agent_config, tools=["read", "grep"])
        mock_response = LLMResponse(text="Hi", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await agent.handle("hello", "s1")
        schemas = mock_llm.call_args[0][2]
        tool_names = {s["function"]["name"] for s in schemas}
        assert tool_names == {"read", "grep"}

    async def test_none_means_all_tools(self, agent_config):
        agent = Agent(agent_config)  # tools=None (default)
        mock_response = LLMResponse(text="Hi", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await agent.handle("hello", "s1")
        schemas = mock_llm.call_args[0][2]
        assert len(schemas) == 10  # all tools including delegate, web_fetch, and schedule


class TestTrimHistory:
    """Unit tests for _trim_history — message-count-based trimming."""

    def test_keeps_target_message_count(self):
        from src.agent.agent import _trim_history
        history = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
            {"role": "assistant", "content": "a3"},
        ]
        _trim_history(history, target_messages=2)
        assert len(history) <= 2
        # Most recent user message must survive.
        assert any(m["role"] == "user" and m["content"] == "u3" for m in history)

    def test_keeps_at_least_one_user_message(self):
        from src.agent.agent import _trim_history
        history = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
        _trim_history(history, target_messages=0)
        assert any(m["role"] == "user" for m in history)

    def test_single_user_session_trims_tail_groups(self):
        from src.agent.agent import _trim_history
        history = [
            {"role": "user", "content": "task"},
            {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "bash", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "out1"},
            {"role": "assistant", "tool_calls": [{"id": "c2", "function": {"name": "bash", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c2", "content": "out2"},
        ]
        _trim_history(history, target_messages=1)
        # User message must survive
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "task"

    def test_preserves_pair_boundaries(self):
        from src.agent.agent import _trim_history
        history = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
            {"role": "assistant", "content": "a3"},
        ]
        _trim_history(history, target_messages=3)
        # First non-trimmed message should be a user (no orphaned assistant/tool)
        assert history[0]["role"] == "user"


class TestSystemTaskMode:
    async def test_system_task_sends_user_message(self, agent):
        mock_response = LLMResponse(text="Task done.", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            result = await agent.handle("", "sched:test:123", system_task_prompt="Do the thing.")
        assert result == "Task done."
        # Task prompt should be sent as a user message for provider compatibility
        messages = mock_llm.call_args[0][1]
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert len(user_msgs) == 1
        assert "Do the thing." in user_msgs[0]["content"]

    async def test_system_task_prompt_in_user_message(self, agent):
        mock_response = LLMResponse(text="Done", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await agent.handle("", "sched:test:123", system_task_prompt="Check PRs.")
        messages = mock_llm.call_args[0][1]
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert len(user_msgs) == 1
        assert "## Scheduled Task" in user_msgs[0]["content"]
        assert "Check PRs." in user_msgs[0]["content"]

    async def test_system_task_cleans_up_session(self, agent):
        mock_response = LLMResponse(text="Done", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response):
            await agent.handle("", "sched:test:123", system_task_prompt="Do it.")
        # Session should be cleaned up after completion
        assert "sched:test:123" not in agent.sessions

    async def test_system_task_with_tool_calls(self, agent):
        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "echo scheduled"})},
            }],
        )
        text_response = LLMResponse(text="Task complete.", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[tool_response, text_response]):
            result = await agent.handle("", "sched:test:456", system_task_prompt="Run a command.")
        assert result == "Task complete."
        # Session cleaned up after completion
        assert "sched:test:456" not in agent.sessions

    async def test_normal_handle_unchanged(self, agent):
        """Ensure regular user messages still work as before."""
        mock_response = LLMResponse(text="Hello!", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await agent.handle("hi", "normal-session")
        assert result == "Hello!"
        history = agent.sessions["normal-session"]
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "hi"


@pytest.mark.asyncio
async def test_delegate_exchanges_preserved_in_history(agent_config):
    """Delegate tool_call + tool_result messages stay intact in history so
    the orchestrator can use the sub-agent's output."""
    responses = [
        LLMResponse(
            text=None,
            tool_calls=[{
                "id": "tc1",
                "type": "function",
                "function": {
                    "name": "delegate",
                    "arguments": '{"agent": "system", "task": "run uptime", "intent": "report uptime"}',
                },
            }],
        ),
        LLMResponse(text="The system has been up for 5 days.", tool_calls=None),
    ]
    with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=responses):
        with patch("src.agent.agent.execute_tool_call", new_callable=AsyncMock, return_value="uptime: 5 days"):
            agent = Agent(agent_config)
            await agent.handle("how long has this machine been running?", "s1")

    history = agent.sessions["s1"]
    roles = [m["role"] for m in history]
    assert roles == ["user", "assistant", "tool", "assistant"]
    tool_msg = history[2]
    assert tool_msg["tool_call_id"] == "tc1"
    assert tool_msg["content"] == "uptime: 5 days"


class TestAgentInit:
    def test_loads_identity(self, agent):
        assert "test assistant" in agent.static_prompt.lower()

    def test_missing_identity_raises(self, tmp_path, tmp_skills):
        from src.config import AgentConfig
        config = AgentConfig(
            identity_file=tmp_path / "nonexistent.md",
            skills_dir=tmp_skills,
        )
        with pytest.raises(FileNotFoundError):
            Agent(config)


@pytest.mark.asyncio
async def test_orchestrator_injects_agent_enum(tmp_path):
    """When tools=["delegate"], the delegate schema's agent param should
    have an enum populated from agents.yaml."""
    identity = tmp_path / "identity.md"
    identity.write_text("You are a test assistant.")
    agents_file = tmp_path / "agents.yaml"
    agents_file.write_text("files:\n  description: 'File ops'\n  tools: [read]\n  system_prompt: 'Do it.'\nsystem:\n  description: 'Shell'\n  tools: [bash]\n  system_prompt: 'Do it.'\n")

    from src.config import AgentConfig
    config = AgentConfig(
        identity_file=identity,
        context_dir=tmp_path,
        agents_file=agents_file,
    )
    agent = Agent(config, tools=["delegate"])
    schemas = agent._get_tool_schemas()

    delegate_schema = next(s for s in schemas if s["function"]["name"] == "delegate")
    agent_prop = delegate_schema["function"]["parameters"]["properties"]["agent"]
    assert "enum" in agent_prop
    assert sorted(agent_prop["enum"]) == ["files", "system"]
