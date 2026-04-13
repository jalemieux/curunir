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


@pytest.mark.asyncio
async def test_call_llm_passes_max_tokens(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        class _Msg:
            content = "ok"
            tool_calls = None
        class _Choice:
            message = _Msg()
        class _Resp:
            choices = [_Choice()]
            usage = None
        return _Resp()

    monkeypatch.setattr("src.llm.litellm.acompletion", fake_acompletion)
    await call_llm("m", [{"role": "user", "content": "hi"}], [], max_tokens=4096)
    assert captured["max_tokens"] == 4096
