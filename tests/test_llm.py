# tests/test_llm.py
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import litellm
import pytest

import src.llm as llm_module
from src.llm import LLMResponse, call_llm, classify_provider_error, describe_image


class TestClassifyProviderError:
    def test_403_with_key_limit_body_is_quota_exhausted(self):
        exc = litellm.APIError(
            status_code=403,
            message="Key limit exceeded (monthly limit)",
            llm_provider="openrouter",
            model="test",
        )
        result = classify_provider_error(exc)
        assert result is not None
        category, msg = result
        assert category == "quota_exhausted"
        assert "quota" in msg.lower()

    def test_rate_limit_error_is_rate_limited(self):
        exc = litellm.RateLimitError(
            message="Too many requests",
            llm_provider="openrouter",
            model="test",
        )
        result = classify_provider_error(exc)
        assert result is not None
        category, msg = result
        assert category == "rate_limited"
        assert "rate" in msg.lower()

    def test_authentication_error_is_quota_exhausted(self):
        exc = litellm.AuthenticationError(
            message="Invalid API key",
            llm_provider="openrouter",
            model="test",
        )
        result = classify_provider_error(exc)
        assert result is not None
        assert result[0] == "quota_exhausted"

    def test_unknown_exception_returns_none(self):
        assert classify_provider_error(ValueError("nope")) is None

    def test_quota_substring_match_without_status_code(self):
        exc = RuntimeError("insufficient_quota: please add credits")
        result = classify_provider_error(exc)
        assert result is not None
        assert result[0] == "quota_exhausted"

    def test_402_status_is_credits_exhausted(self):
        exc = litellm.APIError(
            status_code=402,
            message="Payment Required",
            llm_provider="openrouter",
            model="test",
        )
        result = classify_provider_error(exc)
        assert result is not None
        category, msg = result
        assert category == "credits_exhausted"
        assert msg == llm_module._CREDITS_MSG

    def test_insufficient_credits_body_is_credits_exhausted(self):
        # The real OpenRouter 402 body string from the incident log.
        exc = RuntimeError(
            "Insufficient credits. Add more using https://openrouter.ai/settings/credits"
        )
        result = classify_provider_error(exc)
        assert result is not None
        category, msg = result
        assert category == "credits_exhausted"
        assert msg == llm_module._CREDITS_MSG

    def test_credits_message_is_provider_neutral(self):
        exc = RuntimeError(
            "Insufficient credits. Add more using https://openrouter.ai/settings/credits"
        )
        _, msg = classify_provider_error(exc)
        assert "openrouter" not in msg.lower()
        assert "http" not in msg.lower()


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


@pytest.mark.asyncio
async def test_usage_extracts_openrouter_billing_dimensions():
    """Non-streaming path populates the OpenRouter-aligned usage fields."""
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        prompt_tokens_details=SimpleNamespace(cached_tokens=80, image_tokens=2, audio_tokens=0),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=20, audio_tokens=0),
        cost=0.0042,
    )
    mock_message = MagicMock()
    mock_message.content = "ok"
    mock_message.tool_calls = None
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]
    mock_response.usage = usage
    mock_response.model = "anthropic/claude-sonnet-4"

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        result = await call_llm("openrouter/anthropic/claude-sonnet-4", [], [])

    assert result.usage.prompt_tokens == 100
    assert result.usage.completion_tokens == 50
    assert result.usage.cached_prompt_tokens == 80
    assert result.usage.reasoning_tokens == 20
    assert result.usage.image_tokens == 2
    assert result.usage.audio_tokens == 0
    assert result.usage.cost_usd == pytest.approx(0.0042)
    assert result.usage.model == "anthropic/claude-sonnet-4"


@pytest.mark.asyncio
async def test_usage_falls_back_to_response_cost():
    """When provider doesn't return upstream `cost`, fall back to LiteLLM's response_cost."""
    usage = SimpleNamespace(
        prompt_tokens=10, completion_tokens=5, total_tokens=15,
    )
    mock_message = MagicMock()
    mock_message.content = "hi"
    mock_message.tool_calls = None
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]
    mock_response.usage = usage
    mock_response.model = "gpt-4"
    mock_response._hidden_params = {"response_cost": 0.001}

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        result = await call_llm("openai/gpt-4", [], [])

    assert result.usage.cost_usd == pytest.approx(0.001)


@pytest.mark.asyncio
async def test_usage_cost_none_when_unavailable():
    """When neither cost nor response_cost is available, cost_usd is None."""
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    mock_message = MagicMock()
    mock_message.content = "hi"
    mock_message.tool_calls = None
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]
    mock_response.usage = usage
    mock_response.model = "local-model"
    mock_response._hidden_params = {}

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        result = await call_llm("local-model", [], [])

    assert result.usage.cost_usd is None


@pytest.mark.asyncio
async def test_openrouter_extra_body_merges_usage_flag():
    """OpenRouter calls add usage.include without clobbering provider.order."""
    mock_message = MagicMock()
    mock_message.content = "ok"
    mock_message.tool_calls = None
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]
    mock_response.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    mock_response.model = "anthropic/claude-sonnet-4"

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        await call_llm(
            "openrouter/anthropic/claude-sonnet-4",
            [{"role": "user", "content": "hi"}],
            [],
            openrouter_provider="anthropic",
        )

    kwargs = mock_litellm.acompletion.call_args.kwargs
    extra_body = kwargs["extra_body"]
    assert extra_body["provider"]["order"] == ["anthropic"]
    assert extra_body["usage"]["include"] is True


@pytest.mark.asyncio
async def test_non_openrouter_omits_usage_flag():
    """Non-OpenRouter calls don't add usage.include; extra_body absent without provider."""
    mock_message = MagicMock()
    mock_message.content = "ok"
    mock_message.tool_calls = None
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]
    mock_response.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    mock_response.model = "anthropic/claude-sonnet-4"

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        await call_llm("anthropic/claude-sonnet-4", [{"role": "user", "content": "hi"}], [])

    kwargs = mock_litellm.acompletion.call_args.kwargs
    assert "extra_body" not in kwargs


@pytest.mark.asyncio
async def test_stream_extracts_billing_dimensions():
    """Streaming path also populates the new usage fields from the final usage chunk."""
    delta_text = MagicMock()
    delta_text.content = "hello"
    delta_text.tool_calls = None
    text_chunk = MagicMock()
    text_chunk.choices = [MagicMock(delta=delta_text)]
    text_chunk.usage = None

    usage_chunk = MagicMock()
    usage_chunk.choices = []
    usage_chunk.usage = SimpleNamespace(
        prompt_tokens=200,
        completion_tokens=100,
        total_tokens=300,
        prompt_tokens_details=SimpleNamespace(cached_tokens=150, image_tokens=0, audio_tokens=0),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=40, audio_tokens=0),
        cost=0.0123,
    )
    usage_chunk.model = "anthropic/claude-sonnet-4"

    received: list[str] = []

    async def on_delta(t: str):
        received.append(t)

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=_async_iter([text_chunk, usage_chunk]))
        result = await call_llm(
            "openrouter/anthropic/claude-sonnet-4", [], [],
            on_text_delta=on_delta,
        )

    assert result.usage.prompt_tokens == 200
    assert result.usage.cached_prompt_tokens == 150
    assert result.usage.reasoning_tokens == 40
    assert result.usage.cost_usd == pytest.approx(0.0123)
    assert result.usage.model == "anthropic/claude-sonnet-4"


@pytest.fixture(autouse=True)
def _clear_description_cache():
    """Reset describe_image's in-process cache between tests."""
    llm_module._description_cache.clear()
    yield
    llm_module._description_cache.clear()


@pytest.mark.asyncio
async def test_describe_image_returns_text(tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nFAKEBYTES")

    mock_message = MagicMock()
    mock_message.content = "A red bicycle leaning against a brick wall."
    mock_message.tool_calls = None
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        text = await describe_image(
            "openai/gpt-4o-mini", str(img), "image/png", "what is this?"
        )

    assert text == "A red bicycle leaning against a brick wall."

    # Verify the multimodal payload that was sent
    call_args = mock_litellm.acompletion.await_args
    messages = call_args.kwargs["messages"]
    assert len(messages) == 1
    blocks = messages[0]["content"]
    assert isinstance(blocks, list)
    assert blocks[0]["type"] == "text"
    assert "what is this?" in blocks[0]["text"]
    assert blocks[1]["type"] == "image_url"
    assert blocks[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_describe_image_caches_by_content_hash(tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nIDENTICAL")

    mock_message = MagicMock()
    mock_message.content = "first description"
    mock_message.tool_calls = None
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        first = await describe_image(
            "vision-model", str(img), "image/png", "describe"
        )
        # Second call with same bytes should not hit the LLM again
        second = await describe_image(
            "vision-model", str(img), "image/png", "describe again"
        )

    assert first == "first description"
    assert second == "first description"
    assert mock_litellm.acompletion.await_count == 1


@pytest.mark.asyncio
async def test_describe_image_different_bytes_calls_llm_each_time(tmp_path):
    img1 = tmp_path / "a.png"
    img1.write_bytes(b"AAAA")
    img2 = tmp_path / "b.png"
    img2.write_bytes(b"BBBB")

    msg_a = MagicMock(content="desc A", tool_calls=None)
    msg_b = MagicMock(content="desc B", tool_calls=None)
    resp_a = MagicMock(choices=[MagicMock(message=msg_a)])
    resp_b = MagicMock(choices=[MagicMock(message=msg_b)])

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(side_effect=[resp_a, resp_b])
        a = await describe_image("m", str(img1), "image/png", "q")
        b = await describe_image("m", str(img2), "image/png", "q")

    assert a == "desc A"
    assert b == "desc B"
    assert mock_litellm.acompletion.await_count == 2
