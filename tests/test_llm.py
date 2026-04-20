# tests/test_llm.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm import LLMResponse, call_llm


@pytest.mark.asyncio
async def test_text_response():
    mock_message = MagicMock()
    mock_message.content = "Hello back"
    mock_message.tool_calls = None

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        result = await call_llm("test-model", [{"role": "user", "content": "hi"}], [])

    assert isinstance(result, LLMResponse)
    assert result.text == "Hello back"
    assert result.tool_calls is None


@pytest.mark.asyncio
async def test_tool_call_response():
    mock_tc = MagicMock()
    mock_tc.id = "call_123"
    mock_tc.function.name = "bash"
    mock_tc.function.arguments = '{"command": "echo hi"}'

    mock_message = MagicMock()
    mock_message.content = None
    mock_message.tool_calls = [mock_tc]

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        result = await call_llm("test-model", [], [])

    assert result.text is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["id"] == "call_123"
    assert result.tool_calls[0]["function"]["name"] == "bash"


@pytest.mark.asyncio
async def test_both_text_and_tool_calls():
    mock_tc = MagicMock()
    mock_tc.id = "call_456"
    mock_tc.function.name = "read"
    mock_tc.function.arguments = '{"file_path": "test.py"}'

    mock_message = MagicMock()
    mock_message.content = "Let me check that file"
    mock_message.tool_calls = [mock_tc]

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        result = await call_llm("test-model", [], [])

    assert result.text == "Let me check that file"
    assert len(result.tool_calls) == 1


def _make_stream_chunk(content: str | None = None, tool_call_deltas: list | None = None,
                       usage: tuple | None = None):
    """Build a mock LiteLLM streaming chunk.

    tool_call_deltas: list of dicts like {"index": 0, "id": "call_x",
        "function": {"name": "bash", "arguments": "{\"cmd\":"}}.
    usage: tuple (prompt_tokens, completion_tokens, total_tokens) or None.
    """
    delta = MagicMock()
    delta.content = content
    if tool_call_deltas is None:
        delta.tool_calls = None
    else:
        tc_mocks = []
        for d in tool_call_deltas:
            tc = MagicMock()
            tc.index = d["index"]
            tc.id = d.get("id")
            fn = d.get("function", {})
            tc.function = MagicMock()
            tc.function.name = fn.get("name")
            tc.function.arguments = fn.get("arguments")
            tc_mocks.append(tc)
        delta.tool_calls = tc_mocks
    choice = MagicMock()
    choice.delta = delta
    chunk = MagicMock()
    chunk.choices = [choice]
    if usage is None:
        chunk.usage = None
    else:
        u = MagicMock()
        u.prompt_tokens, u.completion_tokens, u.total_tokens = usage
        chunk.usage = u
    return chunk


def _async_iter(chunks):
    """Wrap a list as an async iterator that supports `async for`."""
    class _AIter:
        def __init__(self, items):
            self._it = iter(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    return _AIter(chunks)


@pytest.mark.asyncio
async def test_stream_text_fires_callback_per_chunk():
    chunks = [
        _make_stream_chunk(content="Hel"),
        _make_stream_chunk(content="lo "),
        _make_stream_chunk(content="world"),
        _make_stream_chunk(usage=(10, 3, 13)),
    ]

    received: list[str] = []

    async def on_delta(text: str):
        received.append(text)

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=_async_iter(chunks))
        result = await call_llm(
            "test-model", [{"role": "user", "content": "hi"}], [],
            on_text_delta=on_delta,
        )

    assert received == ["Hel", "lo ", "world"]
    assert result.text == "Hello world"
    assert result.tool_calls is None
    assert result.usage.prompt_tokens == 10
    assert result.usage.completion_tokens == 3
    assert result.usage.total_tokens == 13


@pytest.mark.asyncio
async def test_stream_accumulates_tool_call_arguments():
    chunks = [
        _make_stream_chunk(content="Let me check"),
        _make_stream_chunk(tool_call_deltas=[
            {"index": 0, "id": "call_abc",
             "function": {"name": "bash", "arguments": "{\"comm"}},
        ]),
        _make_stream_chunk(tool_call_deltas=[
            {"index": 0, "function": {"arguments": "and\": \"echo hi\"}"}},
        ]),
        _make_stream_chunk(usage=(20, 5, 25)),
    ]

    async def on_delta(text: str):
        pass

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=_async_iter(chunks))
        result = await call_llm("test-model", [], [], on_text_delta=on_delta)

    assert result.text == "Let me check"
    assert result.tool_calls is not None
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc["id"] == "call_abc"
    assert tc["function"]["name"] == "bash"
    assert tc["function"]["arguments"] == '{"command": "echo hi"}'
