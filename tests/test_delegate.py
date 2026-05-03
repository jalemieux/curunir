import json
from unittest.mock import AsyncMock, patch

import pytest

from src.llm import LLMResponse


class TestDelegate:
    async def test_returns_sub_agent_response(self, agent_config):
        from src.tools.delegate import exec_delegate

        mock_response = LLMResponse(text="Summary: doc is about X", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await exec_delegate(
                {"task": "Summarize the document"},
                agent_config,
            )
        assert "Summary" in result

    async def test_sub_agent_cannot_delegate(self, agent_config):
        """Sub-agents must not have the delegate tool available."""
        from src.tools.delegate import exec_delegate

        mock_response = LLMResponse(text="Done", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await exec_delegate({"task": "do something"}, agent_config)

        # Check schemas passed to call_llm don't include delegate
        schemas = mock_llm.call_args[0][2]
        tool_names = [s["function"]["name"] for s in schemas]
        assert "delegate" not in tool_names

    async def test_image_paths_inlined_as_base64(self, agent_config, tmp_path):
        from src.tools.delegate import exec_delegate

        # Create a tiny 1x1 PNG
        import base64
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        img_path = tmp_path / "test.png"
        img_path.write_bytes(png_bytes)

        mock_response = LLMResponse(text="It's a white pixel", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await exec_delegate(
                {"task": "Describe the image", "image_paths": [str(img_path)]},
                agent_config,
            )

        # The first message should have multimodal content blocks
        messages = mock_llm.call_args[0][1]
        user_msg = [m for m in messages if m["role"] == "user"][0]
        assert isinstance(user_msg["content"], list)
        assert any(b.get("type") == "image_url" for b in user_msg["content"])

    async def test_max_iterations_respected(self, agent_config):
        from src.tools.delegate import exec_delegate

        agent_config.max_iterations = 2
        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_loop",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "echo loop"})},
            }],
        )
        summary_response = LLMResponse(text="partial work summary", tool_calls=None)
        with patch(
            "src.agent.agent.call_llm",
            new_callable=AsyncMock,
            side_effect=[tool_response, tool_response, summary_response],
        ):
            result = await exec_delegate({"task": "loop"}, agent_config)
        assert "iteration cap reached" in result.lower()

    async def test_empty_task_returns_error(self, agent_config):
        from src.tools.delegate import exec_delegate
        result = await exec_delegate({"task": ""}, agent_config)
        assert "error" in result.lower()

    async def test_invalid_image_paths_type_handled(self, agent_config):
        """If LLM sends image_paths as a string instead of list, handle gracefully."""
        from src.tools.delegate import exec_delegate

        mock_response = LLMResponse(text="Done", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            result = await exec_delegate(
                {"task": "describe", "image_paths": "/tmp/img.png"},
                agent_config,
            )
        assert isinstance(result, str)
        # Verify the string was coerced to a list and treated as an image path
        messages = mock_llm.call_args[0][1]
        user_msg = [m for m in messages if m["role"] == "user"][0]
        # Content should be a list (multimodal) since image_paths was provided
        assert isinstance(user_msg["content"], list)
