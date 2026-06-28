# tests/test_agent.py
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.agent import conversation_store as cs
from src.agent.agent import Agent, _cap_tool_result, _estimate_chars, _is_context_overflow, _trim_history
from src.llm import LLMResponse


@pytest.fixture
def agent(agent_config):
    return Agent(agent_config)


def _real_user_msgs(messages):
    """User messages sent to the LLM, excluding the ephemeral per-turn
    'Current date/time:' note that handle() appends as a trailing user note."""
    out = []
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str) and content.startswith("Current date/time:"):
            continue
        out.append(m)
    return out


class TestConversationPersistence:
    def test_construction_does_not_bulk_load_conversations(self, agent_config):
        """Past conversations on disk are NOT rehydrated into agent.sessions."""
        cs.save(agent_config.context_dir, "old-sess",
                [{"role": "user", "content": "hi"}])
        agent = Agent(agent_config)
        assert agent.sessions == {}

    def test_conversations_snapshot_lists_persisted_conversations(self, agent):
        cs.save(agent.config.context_dir, "a", [{"role": "user", "content": "first"}])
        cs.save(agent.config.context_dir, "b", [{"role": "user", "content": "second"}])
        snap = agent.conversations_snapshot()
        assert {c["session_id"] for c in snap} == {"a", "b"}
        assert all("history" not in c for c in snap)

    def test_conversations_snapshot_excludes_email_conversations(self, agent):
        cs.save(agent.config.context_dir, "web", [{"role": "user", "content": "hi"}],
                channel="ws")
        cs.save(agent.config.context_dir, "mail", [{"role": "user", "content": "hi"}],
                channel="email")
        cs.save(agent.config.context_dir, "legacy", [{"role": "user", "content": "hi"}])
        snap = agent.conversations_snapshot()
        assert {c["session_id"] for c in snap} == {"web", "legacy"}

    def test_conversations_snapshot_excludes_scratch(self, agent):
        """The Scratch slot is ephemeral and never belongs in the sidebar list.

        If a scratch transcript ever leaked to disk (regression check), the
        snapshot must still hide it — the portal renders Scratch as its own
        pinned slot, not as a conversation row.
        """
        cs.save(agent.config.context_dir, "web", [{"role": "user", "content": "hi"}],
                channel="portal")
        cs.save(agent.config.context_dir, "scratch", [{"role": "user", "content": "hi"}],
                channel="portal")
        snap = agent.conversations_snapshot()
        assert {c["session_id"] for c in snap} == {"web"}

    def test_history_snapshot_lazy_loads_unknown_session(self, agent):
        """history_snapshot for a session absent from memory loads it from disk."""
        cs.save(agent.config.context_dir, "disk-only", [
            {"role": "user", "content": "remembered question"},
            {"role": "assistant", "content": "remembered answer"},
        ])
        assert "disk-only" not in agent.sessions
        snap = agent.history_snapshot("disk-only")
        assert [m["content"] for m in snap] == [
            "remembered question", "remembered answer"]

    async def test_handle_lazy_loads_prior_history_to_resume(self, agent):
        """Resuming a persisted conversation continues its prior history."""
        cs.save(agent.config.context_dir, "resumed", [
            {"role": "user", "content": "earlier turn"},
            {"role": "assistant", "content": "earlier reply"},
        ])
        resp = LLMResponse(text="new reply", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=resp):
            await agent.handle("new turn", "resumed")
        history = agent.sessions["resumed"]
        assert history[0]["content"] == "earlier turn"
        assert history[-2]["content"] == "new turn"
        assert history[-1]["content"] == "new reply"


class TestAgentHandle:
    async def test_returns_text_response(self, agent):
        mock_response = LLMResponse(text="Hello!", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await agent.handle("hi", "test-session")
        assert result == "Hello!"

    async def test_text_only_response_exits_after_one_iteration(self, agent):
        """A text-only response must exit the agent loop immediately.

        Regression for the loop-exit bug where a usage_store refactor moved
        the `if response.text: return` block outside the for-loop, causing
        the agent to re-call the LLM up to max_iterations times (and stream
        the same answer N times to the client) before returning
        'Iteration limit reached.'
        """
        mock_response = LLMResponse(text="done", tool_calls=None)
        with patch(
            "src.agent.agent.call_llm",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_call:
            result = await agent.handle("hi", "s1")
        assert result == "done"
        assert mock_call.call_count == 1

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
        # The combined response should have content preserved
        assert agent.sessions["s1"][1].get("content") == "Let me check"
        assert agent.sessions["s1"][1].get("tool_calls") is not None

    async def test_unparseable_tool_arguments_do_not_crash(self, agent):
        bad_tool = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_bad",
                "type": "function",
                "function": {"name": "write", "arguments": '{"file_path": "/tmp/x", "content": "unterminated'},
            }],
        )
        recovery = LLMResponse(text="recovered", tool_calls=None)

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[bad_tool, recovery]):
            result = await agent.handle("write something", "s1")

        assert result == "recovered"
        history = agent.sessions["s1"]
        tool_msg = next(m for m in history if m.get("role") == "tool")
        assert tool_msg["tool_call_id"] == "call_bad"
        assert "not valid JSON" in tool_msg["content"]

    async def test_tool_executor_exception_does_not_crash(self, agent):
        # A tool executor raising an unanticipated exception must become a
        # model-visible tool error, not propagate out of handle() and kill the
        # turn (the systemic backstop for the IDNA "label too long" crash).
        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_boom",
                "type": "function",
                "function": {"name": "web_fetch", "arguments": json.dumps({"url": "https://example.com"})},
            }],
        )
        recovery = LLMResponse(text="recovered after tool error", tool_calls=None)

        async def boom(*a, **k):
            raise RuntimeError("label too long")

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[tool_response, recovery]), \
             patch("src.agent.agent.execute_tool_call", new=boom):
            result = await agent.handle("fetch something", "s1")

        assert result == "recovered after tool error"
        history = agent.sessions["s1"]
        tool_msg = next(m for m in history if m.get("role") == "tool")
        assert tool_msg["tool_call_id"] == "call_boom"
        assert "web_fetch" in tool_msg["content"]
        assert "label too long" in tool_msg["content"]

    async def test_empty_response_returns_error(self, agent):
        empty = LLMResponse(text=None, tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=empty) as mock_call:
            result = await agent.handle("hello", "s1")
        assert "error" in result.lower()
        # Empty triggers retry-then-nudge: 3 LLM calls before surfacing error.
        assert mock_call.await_count == 3

    async def test_empty_response_retries_once_and_recovers(self, agent):
        """A single transient empty response is retried and the second result is used."""
        empty = LLMResponse(text=None, tool_calls=None)
        good = LLMResponse(text="recovered", tool_calls=None)
        with patch(
            "src.agent.agent.call_llm",
            new_callable=AsyncMock,
            side_effect=[empty, good],
        ) as mock_call:
            result = await agent.handle("hi", "s1")
        assert result == "recovered"
        assert mock_call.await_count == 2
        # No "Continue." nudge should have been appended since retry succeeded.
        user_msgs = [m for m in agent.sessions["s1"] if m.get("role") == "user"]
        assert all(m.get("content") != "Continue." for m in user_msgs)

    async def test_empty_response_nudges_with_continue_after_retry_fails(self, agent):
        """Two empty responses in a row trigger a 'Continue.' nudge before the third call."""
        empty = LLMResponse(text=None, tool_calls=None)
        good = LLMResponse(text="kept going", tool_calls=None)

        seen_messages: list[list[dict]] = []

        async def fake_call_llm(model, messages, tools, **kw):
            seen_messages.append(list(messages))
            return [empty, empty, good][len(seen_messages) - 1]

        with patch("src.agent.agent.call_llm", new=fake_call_llm):
            result = await agent.handle("hi", "s1")

        assert result == "kept going"
        assert len(seen_messages) == 3
        # Third call must include the "Continue." nudge as the last real user
        # turn (the trailing live-time note sits after it, outside history).
        assert _real_user_msgs(seen_messages[2])[-1] == {"role": "user", "content": "Continue."}
        # First two calls must NOT include the nudge yet.
        assert _real_user_msgs(seen_messages[0])[-1].get("content") != "Continue."
        assert _real_user_msgs(seen_messages[1])[-1].get("content") != "Continue."

    async def test_empty_response_after_attachment_returns_empty(self, agent):
        """If the agent already attached a file this turn, an empty terminal
        response is fine — the attachment is the reply, not an error."""
        empty = LLMResponse(text=None, tool_calls=None)
        attachments = [{"filename": "report.md", "path": "/tmp/report.md",
                        "mime_type": "text/markdown", "size": 42}]
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=empty):
            result = await agent.handle("hi", "s1", attachments=attachments)
        assert result == ""

    async def test_quota_exceeded_returns_friendly_message(self, agent):
        """A 403 quota error from the LLM provider should surface a friendly
        message instead of bubbling up as a generic processing error."""
        import litellm

        exc = litellm.APIError(
            status_code=403,
            message="Key limit exceeded (monthly limit)",
            llm_provider="openrouter",
            model="test",
        )
        with patch(
            "src.agent.agent.call_llm",
            new_callable=AsyncMock,
            side_effect=exc,
        ):
            result = await agent.handle("hi", "s1")
        assert "quota" in result.lower()

    async def test_insufficient_credits_returns_friendly_message(self, agent):
        """A 402 insufficient-credits error should surface a friendly, provider-
        neutral message instead of the generic processing error."""
        import litellm

        exc = litellm.APIError(
            status_code=402,
            message="Insufficient credits. Add more using https://openrouter.ai/settings/credits",
            llm_provider="openrouter",
            model="test",
        )
        with patch(
            "src.agent.agent.call_llm",
            new_callable=AsyncMock,
            side_effect=exc,
        ):
            result = await agent.handle("hi", "s1")
        assert "credits" in result.lower()
        assert "openrouter" not in result.lower()

    async def test_request_cancel_returns_false_when_no_session_running(self, agent):
        assert agent.request_cancel("nope") is False

    async def test_cancel_interrupts_loop_between_iterations(self, agent_config):
        """Setting the cancel event mid-flight stops the loop on the next iteration."""
        agent_config.max_iterations = 10
        agent = Agent(agent_config)

        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_x",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "echo x"})},
            }],
        )

        call_count = 0

        async def fake_call_llm(*a, **kw):
            nonlocal call_count
            call_count += 1
            # Trigger interrupt after the first LLM call returns; the second
            # iteration should observe it at the top of the loop and stop.
            if call_count == 1:
                agent.request_cancel("s1")
            return tool_response

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=fake_call_llm):
            result = await agent.handle("run", "s1")

        assert result == "(interrupted)"
        assert call_count == 1
        # Cleanup happened
        assert "s1" not in agent._running_sessions
        assert not agent._cancel_events["s1"].is_set()

    async def test_cancel_mid_batch_pads_remaining_tools_with_stubs(self, agent_config):
        """When cancel fires mid-batch, the remaining tool calls in the same
        batch are stubbed with '(interrupted)' so every tool_call_id has a
        matching tool response — required by the chat schema."""
        agent_config.max_iterations = 10
        agent = Agent(agent_config)

        tool_response = LLMResponse(
            text=None,
            tool_calls=[
                {"id": "c1", "type": "function",
                 "function": {"name": "bash", "arguments": json.dumps({"command": "echo 1"})}},
                {"id": "c2", "type": "function",
                 "function": {"name": "bash", "arguments": json.dumps({"command": "echo 2"})}},
                {"id": "c3", "type": "function",
                 "function": {"name": "bash", "arguments": json.dumps({"command": "echo 3"})}},
            ],
        )

        execute_call_count = 0

        async def fake_execute(name, args, config, attachments=None, on_tool_call=None):
            nonlocal execute_call_count
            execute_call_count += 1
            if execute_call_count == 1:
                agent.request_cancel("s1")
            return f"result-{execute_call_count}"

        with patch("src.agent.agent.execute_tool_call", new=fake_execute), \
             patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=tool_response):
            result = await agent.handle("run", "s1")

        assert result == "(interrupted)"
        assert execute_call_count == 1  # only first tool actually ran

        history = agent.sessions["s1"]
        tool_msgs = [m for m in history if m.get("role") == "tool"]
        assert len(tool_msgs) == 3
        assert tool_msgs[0]["tool_call_id"] == "c1"
        assert tool_msgs[0]["content"] == "result-1"
        assert tool_msgs[1] == {"role": "tool", "tool_call_id": "c2", "content": "(interrupted)"}
        assert tool_msgs[2] == {"role": "tool", "tool_call_id": "c3", "content": "(interrupted)"}
        assert history[-1] == {"role": "assistant", "content": "(interrupted)"}

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

    async def test_forwards_on_text_delta_to_call_llm(self, agent):
        mock_response = LLMResponse(text="streamed", tool_calls=None)

        async def cb(text: str):
            pass

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_call:
            await agent.handle("hi", "test-session", on_text_delta=cb)

        assert mock_call.call_count == 1
        kwargs = mock_call.call_args.kwargs
        assert kwargs.get("on_text_delta") is cb

    async def test_forwards_on_text_delta_across_iterations(self, agent):
        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "echo hi"})},
            }],
        )
        text_response = LLMResponse(text="Done!", tool_calls=None)

        async def cb(text: str):
            pass

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[tool_response, text_response]) as mock_call:
            await agent.handle("run", "s1", on_text_delta=cb)

        assert mock_call.call_count == 2
        for call in mock_call.call_args_list:
            assert call.kwargs.get("on_text_delta") is cb

    async def test_writes_usage_row_per_call_llm(self, agent_config):
        from src.llm import LLMUsage

        records: list = []

        class StubStore:
            def record(self, row):
                records.append(row)

        agent = Agent(agent_config, usage_store=StubStore())

        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "echo hi"})},
            }],
            usage=LLMUsage(prompt_tokens=10, completion_tokens=2, model="m1", cost_usd=0.001),
        )
        text_response = LLMResponse(
            text="done",
            tool_calls=None,
            usage=LLMUsage(prompt_tokens=20, completion_tokens=5, model="m1", cost_usd=0.002),
        )

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[tool_response, text_response]):
            await agent.handle("hi", "sess-x")

        assert len(records) == 2
        assert all(r.session_id == "sess-x" for r in records)
        assert records[0].model == "m1"
        assert records[0].prompt_tokens == 10
        assert records[1].completion_tokens == 5

    async def test_usage_store_failure_does_not_break_response(self, agent_config):
        class BrokenStore:
            def record(self, row):
                raise RuntimeError("disk full")

        agent = Agent(agent_config, usage_store=BrokenStore())

        text_response = LLMResponse(text="ok", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=text_response):
            result = await agent.handle("hi", "s1")
        assert result == "ok"

    async def test_accepts_list_content_and_forwards_to_llm(self, agent):
        captured: dict = {}

        async def fake_call_llm(model, messages, tools, **kwargs):
            captured["messages"] = messages
            return LLMResponse(text="ack", tool_calls=None)

        content_blocks = [
            {"type": "text", "text": "describe this"},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]

        with patch("src.agent.agent.call_llm", new=fake_call_llm):
            result = await agent.handle(content_blocks, "s1")

        assert result == "ack"
        assert agent.sessions["s1"][0]["content"] == content_blocks
        user_msg = _real_user_msgs(captured["messages"])[-1]
        assert user_msg["content"] == content_blocks


class TestTrimHistoryMultimodal:
    def test_image_block_costs_fixed_amount(self):
        big_url = "data:image/png;base64," + ("A" * 500_000)
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": big_url}},
            ],
        }
        # text "hi" = 2 chars, image = 2000 chars, total = 2002
        assert _estimate_chars([msg]) == 2 + 2000

    def test_trim_keeps_recent_multimodal_messages(self):
        big_url = "data:image/png;base64," + ("A" * 10)
        history = [
            {"role": "user", "content": "old message " * 1000},
            {"role": "assistant", "content": "old reply " * 1000},
            {"role": "user", "content": [
                {"type": "text", "text": "recent"},
                {"type": "image_url", "image_url": {"url": big_url}},
            ]},
            {"role": "assistant", "content": "recent reply"},
        ]
        _trim_history(history, max_chars=5_000)
        assert len(history) == 2
        assert history[0]["content"][0]["text"] == "recent"


class TestIsContextOverflow:
    def test_openrouter_max_context_message(self):
        import litellm

        msg = (
            "This endpoint's maximum context length is 128000 tokens. "
            "However, you requested about 1900000 tokens (1899000 of text "
            "input, 1000 in the system prompt). Please reduce the length "
            "of either one."
        )
        exc = litellm.BadRequestError(
            message=msg,
            model="openrouter/anthropic/claude-3.5-sonnet",
            llm_provider="openrouter",
        )
        assert _is_context_overflow(exc) is True

    def test_context_window_exceeded_error(self):
        import litellm

        exc = litellm.ContextWindowExceededError(
            message="too long",
            model="gpt-4",
            llm_provider="openai",
        )
        assert _is_context_overflow(exc) is True

    def test_generic_context_limit_message(self):
        assert _is_context_overflow(Exception("context limit reached")) is True

    def test_exceeds_tokens_message(self):
        assert (
            _is_context_overflow(
                Exception("This exceeds the maximum number of tokens allowed")
            )
            is True
        )

    def test_prompt_too_long_message(self):
        assert _is_context_overflow(Exception("prompt is too long")) is True

    def test_unrelated_error_not_overflow(self):
        assert _is_context_overflow(ValueError("nope")) is False


class TestDelegateToolExecution:
    async def test_delegate_via_agent_handle(self, agent):
        """Delegate tool calls go through the unified execute_tool_call."""
        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_delegate",
                "type": "function",
                "function": {"name": "delegate", "arguments": json.dumps({"task": "say hello"})},
            }],
        )
        text_response = LLMResponse(text="Done", tool_calls=None)

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[tool_response, text_response]), \
             patch("src.agent.agent.execute_tool_call", new_callable=AsyncMock, return_value="sub-agent result"):
            result = await agent.handle("delegate this", "s1")

        assert result == "Done"
        # Verify the tool result was recorded in history
        history = agent.sessions["s1"]
        tool_msg = [m for m in history if m["role"] == "tool"][0]
        assert tool_msg["content"] == "sub-agent result"


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
        assert len(schemas) == 11  # all default tools including attach


class TestHistoryTruncation:
    async def test_trims_old_messages_when_over_limit(self, agent_config):
        agent = Agent(agent_config)
        session_id = "s-trunc"
        history = agent.sessions.setdefault(session_id, [])
        for i in range(100):
            history.append({"role": "user", "content": "x" * 10_000})
            history.append({"role": "assistant", "content": "y" * 10_000})

        mock_response = LLMResponse(text="ok", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await agent.handle("new message", "s-trunc")

        messages = mock_llm.call_args[0][1]
        # Should be trimmed (system + trimmed history + new user msg)
        assert len(messages) < 202

    async def test_no_trim_when_single_user_message(self, agent_config):
        """Sub-agents have one user message — trimming must not remove it."""
        agent = Agent(agent_config)
        session_id = "s-single"
        history = agent.sessions.setdefault(session_id, [])
        # Simulate sub-agent: one user msg, then a huge tool result
        history.append({"role": "user", "content": "analyze this"})
        history.append({"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "read", "arguments": "{}"}}]})
        history.append({"role": "tool", "tool_call_id": "c1", "content": "x" * 1_000_000})

        mock_response = LLMResponse(text="ok", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await agent.handle("continue", session_id)

        messages = mock_llm.call_args[0][1]
        non_system = [m for m in messages if m["role"] != "system"]
        # Must still have at least one user message
        assert any(m["role"] == "user" for m in non_system)

    async def test_truncation_preserves_message_pairs(self, agent_config):
        """Truncation should not leave orphaned tool results or split pairs."""
        agent = Agent(agent_config)
        session_id = "s-pairs"
        history = agent.sessions.setdefault(session_id, [])
        for i in range(50):
            history.append({"role": "user", "content": "x" * 20_000})
            history.append({"role": "assistant", "content": "y" * 20_000})

        mock_response = LLMResponse(text="ok", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await agent.handle("new message", "s-pairs")

        messages = mock_llm.call_args[0][1]
        # After system prompt, first message should be "user" (not orphaned assistant/tool)
        non_system = [m for m in messages if m["role"] != "system"]
        assert non_system[0]["role"] == "user"

    async def test_system_task_trims_tool_groups_after_user_prompt(self, agent_config):
        """System tasks (single user message) should trim old tool groups, not the task prompt."""
        agent = Agent(agent_config)
        session_id = "sched:trim:1"
        history = agent.sessions.setdefault(session_id, [])
        # Simulate: task prompt + many large tool call rounds
        history.append({"role": "user", "content": "## Scheduled Task\nDo something."})
        for i in range(20):
            history.append({"role": "assistant", "tool_calls": [{"id": f"c{i}", "function": {"name": "bash", "arguments": "{}"}}]})
            history.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 100_000})

        mock_response = LLMResponse(text="done", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            result = await agent.handle("", session_id, system_task_prompt="Do something.")

        messages = mock_llm.call_args[0][1]
        non_system = [m for m in messages if m["role"] != "system"]
        # Task prompt must survive trimming
        assert non_system[0]["role"] == "user"
        assert "Scheduled Task" in non_system[0]["content"]
        # History should have been trimmed (less than original 41 messages)
        assert len(non_system) < 41


class TestSystemTaskMode:
    async def test_system_task_sends_user_message(self, agent):
        mock_response = LLMResponse(text="Task done.", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            result = await agent.handle("", "sched:test:123", system_task_prompt="Do the thing.")
        assert result == "Task done."
        # Task prompt should be sent as a user message for provider compatibility
        messages = mock_llm.call_args[0][1]
        user_msgs = _real_user_msgs(messages)
        assert len(user_msgs) == 1
        assert "Do the thing." in user_msgs[0]["content"]

    async def test_system_task_prompt_in_user_message(self, agent):
        mock_response = LLMResponse(text="Done", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await agent.handle("", "sched:test:123", system_task_prompt="Check PRs.")
        messages = mock_llm.call_args[0][1]
        user_msgs = _real_user_msgs(messages)
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


class TestOnboardingGate:
    """Hard gate fires on first un-onboarded turn; passes through otherwise."""

    async def test_gate_fires_when_identity_missing_and_empty_history(self, agent):
        """First user message + no identity.md → message rewritten to onboarding directive."""
        agent.config.identity_file.unlink()  # un-onboarded: no personality layer yet
        mock_response = LLMResponse(text="welcome", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await agent.handle("hi", "s1")
        # Inspect the user message that was sent to the LLM.
        messages = mock_llm.call_args[0][1]
        user_msgs = _real_user_msgs(messages)
        assert len(user_msgs) == 1
        assert "isn't onboarded yet" in user_msgs[0]["content"]
        assert "`onboarding` skill" in user_msgs[0]["content"]

    async def test_gate_stays_quiet_when_identity_exists(self, agent):
        """If identity.md exists, user's first message passes through unchanged."""
        # The agent fixture's context already has identity.md (= onboarded).
        mock_response = LLMResponse(text="ok", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await agent.handle("hi", "s1")
        user_msgs = [m for m in mock_llm.call_args[0][1] if m["role"] == "user"]
        assert user_msgs[0]["content"] == "hi"

    async def test_gate_stays_quiet_when_history_nonempty(self, agent):
        """Second turn — history non-empty — even without marker, passes through."""
        # Seed history with one prior turn pair so len(history) > 0 at gate time.
        agent.sessions["s1"] = [
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "earlier reply"},
        ]
        mock_response = LLMResponse(text="next", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await agent.handle("follow-up question", "s1")
        user_msgs = _real_user_msgs(mock_llm.call_args[0][1])
        # Latest user message preserved verbatim.
        assert user_msgs[-1]["content"] == "follow-up question"

    async def test_gate_stays_quiet_for_system_task(self, agent):
        """Scheduled tasks must not be hijacked by the gate."""
        mock_response = LLMResponse(text="done", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await agent.handle("", "sched:job:1", system_task_prompt="Do the digest.")
        user_msgs = [m for m in mock_llm.call_args[0][1] if m["role"] == "user"]
        assert "## Scheduled Task" in user_msgs[0]["content"]
        assert "Do the digest." in user_msgs[0]["content"]
        assert "onboarded" not in user_msgs[0]["content"].lower()


class TestAgentInit:
    def test_loads_identity(self, agent):
        assert "test assistant" in agent.static_prompt.lower()

    def test_missing_identity_boots_with_warning(self, tmp_path, tmp_skills, caplog, monkeypatch):
        from src.config import AgentConfig
        monkeypatch.setattr("src.persona.PERSONAS_DIR", tmp_path / "personas")
        config = AgentConfig(
            identity_file=tmp_path / "nonexistent.md",
            persona="no-such-persona",
            skill_dirs=[tmp_skills],
        )
        with caplog.at_level("WARNING"):
            agent = Agent(config)
        assert "Identity file not found" in caplog.text
        assert agent is not None


class TestSystemPromptCaching:
    async def test_system_prompt_is_stable_across_calls(self, agent):
        """Auto-cache providers hash the system prefix — it must be byte-identical
        between calls so the cache hits on the second iteration onward."""
        mock_response = LLMResponse(text="ok", tool_calls=None)
        captured: list[str] = []

        async def fake_call_llm(model, messages, tools, **kwargs):
            captured.append(messages[0]["content"])
            return mock_response

        with patch("src.agent.agent.call_llm", new=fake_call_llm):
            await agent.handle("first", "s1")
            await agent.handle("second", "s1")

        assert len(captured) == 2
        assert captured[0] == captured[1], "system prompt mutated between calls"

    async def test_static_prompt_has_no_boot_timestamp(self, agent):
        """The static prefix is truly static now — no boot timestamp baked in,
        so it stays byte-stable across every session and process lifetime."""
        assert "Conversation started at" not in agent.static_prompt
        assert not hasattr(agent, "_boot_time")

    async def test_session_prompt_carries_started_at(self, agent):
        """The per-session prompt appends a stable 'Conversation started at'
        line, sourced from the session's created_at (cache-safe within a
        session)."""
        prompt = agent._get_session_prompt("s1")
        assert "Conversation started at:" in prompt
        # Timezone-aware ISO (offset or 'Z' / '+'), never a naive timestamp.
        started_line = next(
            line for line in prompt.splitlines()
            if line.startswith("Conversation started at:")
        )
        assert ("+" in started_line) or ("-" in started_line.split("T", 1)[1]) or started_line.endswith("Z")

    async def test_session_prompt_started_at_stable_within_session(self, agent):
        """Cached per session like the memory block — byte-stable across turns."""
        first = agent._get_session_prompt("s1")
        second = agent._get_session_prompt("s1")
        assert first == second

    async def test_resumed_session_keeps_original_created_at(self, agent):
        """A resumed conversation shows its persisted created_at, not 'now'."""
        from datetime import datetime, timezone

        old = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        cs.save(agent.config.context_dir, "resumed",
                [{"role": "user", "content": "hi"}], now=old)
        prompt = agent._get_session_prompt("resumed")
        assert old.isoformat() in prompt

    async def test_started_at_distinct_per_session(self, agent):
        """Two different sessions carry their own started-at values."""
        from datetime import datetime, timezone

        a = datetime(2025, 1, 1, tzinfo=timezone.utc)
        b = datetime(2026, 6, 6, tzinfo=timezone.utc)
        cs.save(agent.config.context_dir, "sa", [{"role": "user", "content": "x"}], now=a)
        cs.save(agent.config.context_dir, "sb", [{"role": "user", "content": "y"}], now=b)
        pa = agent._get_session_prompt("sa")
        pb = agent._get_session_prompt("sb")
        assert a.isoformat() in pa
        assert b.isoformat() in pb
        assert pa != pb

    async def test_forget_session_drops_started_at_cache(self, agent):
        """After a clear, the next (distinct) conversation must not reuse the
        previous one's cached started-at line."""
        from datetime import datetime, timezone

        old = datetime(2025, 1, 1, tzinfo=timezone.utc)
        cs.save(agent.config.context_dir, "s", [{"role": "user", "content": "x"}], now=old)
        assert old.isoformat() in agent._get_session_prompt("s")

        # Clear: delete the record and forget in-memory state, as run.py does.
        cs.delete(agent.config.context_dir, "s")
        agent.forget_session("s")

        new = datetime(2026, 6, 6, tzinfo=timezone.utc)
        cs.save(agent.config.context_dir, "s", [{"role": "user", "content": "y"}], now=new)
        prompt = agent._get_session_prompt("s")
        assert new.isoformat() in prompt
        assert old.isoformat() not in prompt

    async def test_live_time_note_injected_not_persisted(self, agent):
        """A 'Current date/time:' note rides as the final message of the LLM
        request but is never written into stored history."""
        captured: list[list] = []

        async def fake_call_llm(model, messages, tools, **kwargs):
            captured.append([dict(m) for m in messages])
            return LLMResponse(text="ok", tool_calls=None)

        with patch("src.agent.agent.call_llm", new=fake_call_llm):
            await agent.handle("hello", "s1")

        sent = captured[0]
        assert "Current date/time:" in sent[-1]["content"]
        # Not persisted into history.
        assert all(
            "Current date/time:" not in (m.get("content") or "")
            for m in agent.sessions["s1"]
        )

    async def test_live_time_note_reflects_turn_start(self, agent):
        """The live note's timestamp is datetime.now() captured at turn start."""
        import src.agent.agent as agent_mod
        from datetime import datetime, timedelta, timezone

        real_datetime = agent_mod.datetime
        fixed = real_datetime(2026, 6, 25, 14, 30, 0, tzinfo=timezone(timedelta(hours=-4)))

        class FakeDatetime:
            @staticmethod
            def now(tz=None):
                return fixed if tz is None else real_datetime.now(tz)

        captured: list[list] = []

        async def fake_call_llm(model, messages, tools, **kwargs):
            captured.append([dict(m) for m in messages])
            return LLMResponse(text="ok", tool_calls=None)

        with patch.object(agent_mod, "datetime", FakeDatetime), \
                patch("src.agent.agent.call_llm", new=fake_call_llm):
            await agent.handle("hi", "s1")

        note = captured[0][-1]["content"]
        # Production does datetime.now().astimezone() — re-expressed in local tz,
        # same instant. Compare against that, not the raw fixed offset.
        expected = fixed.astimezone()
        assert expected.isoformat() in note
        assert expected.strftime("%A") in note  # weekday name present
        # tz-aware, never naive.
        assert ("+" in note) or ("-" in note.split("T", 1)[1])

    async def test_live_note_stable_across_tool_loop_iterations(self, agent):
        """Within one handle() turn, the system prompt and the live-time note are
        byte-identical across tool-loop iterations — the cacheable prefix stays
        stable and the suffix note doesn't change mid-turn."""
        responses = [
            LLMResponse(
                text=None,
                tool_calls=[{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"file_path": "x"}'},
                }],
            ),
            LLMResponse(text="done", tool_calls=None),
        ]
        captured: list[list] = []

        async def fake_call_llm(model, messages, tools, **kwargs):
            captured.append([dict(m) for m in messages])
            return responses[len(captured) - 1]

        with patch("src.agent.agent.execute_tool_call", new_callable=AsyncMock, return_value="ok"), \
                patch("src.agent.agent.call_llm", new=fake_call_llm):
            await agent.handle("hi", "s1")

        assert len(captured) == 2
        # System prefix byte-identical across iterations.
        assert captured[0][0]["content"] == captured[1][0]["content"]
        # Live-time note identical within the turn.
        assert captured[0][-1]["content"] == captured[1][-1]["content"]
        assert "Current date/time:" in captured[0][-1]["content"]

    async def test_stats_include_cache_hit_rate(self, agent):
        from src.llm import LLMUsage

        mock_response = LLMResponse(
            text="ok",
            tool_calls=None,
            usage=LLMUsage(
                prompt_tokens=1000,
                completion_tokens=10,
                cached_prompt_tokens=800,
            ),
        )
        metadata: dict = {}
        with patch(
            "src.agent.agent.call_llm",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            await agent.handle("hi", "s1", metadata=metadata)

        stats = metadata["stats"]
        assert stats["cached_prompt_tokens"] == 800
        assert stats["cache_hit_rate"] == 0.8

    async def test_cache_hit_rate_zero_when_no_prompt_tokens(self, agent):
        """No prompt tokens reported → 0.0, not a ZeroDivisionError."""
        from src.llm import LLMUsage

        mock_response = LLMResponse(
            text="ok",
            tool_calls=None,
            usage=LLMUsage(prompt_tokens=0, completion_tokens=0, cached_prompt_tokens=0),
        )
        metadata: dict = {}
        with patch(
            "src.agent.agent.call_llm",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            await agent.handle("hi", "s1", metadata=metadata)

        stats = metadata["stats"]
        assert stats["cached_prompt_tokens"] == 0
        assert stats["cache_hit_rate"] == 0.0

    async def test_cache_hit_rate_aggregates_across_iterations(self, agent):
        """Cache metrics sum across every LLM call in the tool loop."""
        from src.llm import LLMUsage

        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "echo hi"})},
            }],
            usage=LLMUsage(prompt_tokens=500, completion_tokens=5, cached_prompt_tokens=0),
        )
        text_response = LLMResponse(
            text="done",
            tool_calls=None,
            usage=LLMUsage(prompt_tokens=500, completion_tokens=5, cached_prompt_tokens=400),
        )

        metadata: dict = {}
        with patch(
            "src.agent.agent.call_llm",
            new_callable=AsyncMock,
            side_effect=[tool_response, text_response],
        ):
            await agent.handle("hi", "s1", metadata=metadata)

        stats = metadata["stats"]
        assert stats["prompt_tokens"] == 1000
        assert stats["cached_prompt_tokens"] == 400
        assert stats["cache_hit_rate"] == 0.4


class TestCapToolResult:
    """Unit tests for the pure _cap_tool_result helper."""

    def test_under_cap_passthrough(self):
        content = "short result"
        assert _cap_tool_result(content, 100) == content

    def test_exactly_at_cap_passthrough(self):
        content = "x" * 100
        assert _cap_tool_result(content, 100) == content

    def test_one_over_cap_truncates(self):
        content = "x" * 101
        capped = _cap_tool_result(content, 100)
        assert capped.startswith("x" * 100)
        assert "truncated 1 chars" in capped
        assert len(capped) > 100  # marker appended

    def test_far_over_cap_reports_dropped_count(self):
        content = "y" * 500
        capped = _cap_tool_result(content, 100)
        assert capped.startswith("y" * 100)
        assert "truncated 400 chars" in capped

    def test_empty_passthrough(self):
        assert _cap_tool_result("", 100) == ""

    def test_none_passthrough(self):
        assert _cap_tool_result(None, 100) is None


class TestToolResultCapInHistory:
    """The cap is applied to tool results before they enter history."""

    async def test_oversized_tool_result_is_truncated_in_history(self, agent_config):
        agent_config.max_tool_result_chars = 1000
        agent = Agent(agent_config)

        big = "Z" * 50_000
        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_big",
                "type": "function",
                "function": {"name": "read", "arguments": json.dumps({"file_path": "/tmp/x"})},
            }],
        )
        final = LLMResponse(text="done", tool_calls=None)

        with patch("src.agent.agent.execute_tool_call", new_callable=AsyncMock, return_value=big), \
             patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[tool_response, final]):
            result = await agent.handle("read it", "s1")

        assert result == "done"
        history = agent.sessions["s1"]
        tool_msg = next(m for m in history if m.get("role") == "tool")
        # Truncated body + marker only.
        assert len(tool_msg["content"]) <= agent_config.max_tool_result_chars + 200
        assert "truncated 49000 chars" in tool_msg["content"]

    async def test_oversized_result_does_not_reach_provider(self, agent_config):
        agent_config.max_tool_result_chars = 1000
        agent = Agent(agent_config)

        big = "Z" * 50_000
        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_big",
                "type": "function",
                "function": {"name": "read", "arguments": json.dumps({"file_path": "/tmp/x"})},
            }],
        )
        final = LLMResponse(text="done", tool_calls=None)

        mock_call = AsyncMock(side_effect=[tool_response, final])
        with patch("src.agent.agent.execute_tool_call", new_callable=AsyncMock, return_value=big), \
             patch("src.agent.agent.call_llm", new=mock_call):
            await agent.handle("read it", "s1")

        # Second call_llm (post-tool) must not carry the full 50k payload.
        second_call_messages = mock_call.call_args_list[1].kwargs.get("messages") \
            or mock_call.call_args_list[1].args[1]
        tool_entries = [m for m in second_call_messages if m.get("role") == "tool"]
        assert tool_entries
        assert all(len(m["content"]) <= agent_config.max_tool_result_chars + 200 for m in tool_entries)
        assert big not in "".join(m["content"] for m in tool_entries)

    async def test_under_cap_result_stored_verbatim(self, agent_config):
        agent_config.max_tool_result_chars = 100_000
        agent = Agent(agent_config)

        small = "small output here"
        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_small",
                "type": "function",
                "function": {"name": "read", "arguments": json.dumps({"file_path": "/tmp/x"})},
            }],
        )
        final = LLMResponse(text="done", tool_calls=None)

        with patch("src.agent.agent.execute_tool_call", new_callable=AsyncMock, return_value=small), \
             patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[tool_response, final]):
            await agent.handle("read it", "s1")

        history = agent.sessions["s1"]
        tool_msg = next(m for m in history if m.get("role") == "tool")
        assert tool_msg["content"] == small
        assert "truncated" not in tool_msg["content"]
