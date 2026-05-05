from src.tools.dispatcher import execute_tool_call


class TestExecuteToolCall:
    async def test_dispatches_glob(self, tmp_path, agent_config):
        (tmp_path / "x.py").write_text("hi")
        result = await execute_tool_call("glob", {"pattern": "*.py", "path": str(tmp_path)}, agent_config)
        assert "x.py" in result

    async def test_dispatches_read(self, tmp_path, agent_config):
        f = tmp_path / "test.txt"
        f.write_text("content")
        result = await execute_tool_call("read", {"file_path": str(f)}, agent_config)
        assert "content" in result

    async def test_dispatches_write(self, tmp_path, agent_config):
        f = tmp_path / "out.txt"
        await execute_tool_call("write", {"file_path": str(f), "content": "data"}, agent_config)
        assert f.read_text() == "data"

    async def test_dispatches_bash(self, agent_config):
        result = await execute_tool_call("bash", {"command": "echo dispatch_test"}, agent_config)
        assert "dispatch_test" in result

    async def test_unknown_tool(self, agent_config):
        result = await execute_tool_call("nonexistent", {}, agent_config)
        assert "unknown tool" in result.lower()

    async def test_case_insensitive(self, agent_config):
        result = await execute_tool_call("Bash", {"command": "echo case_test"}, agent_config)
        assert "case_test" in result

    async def test_dispatches_schedule(self, tmp_path, agent_config):
        agent_config.context_dir = tmp_path
        result = await execute_tool_call(
            "schedule", {"action": "list"}, agent_config,
        )
        assert "no scheduled tasks" in result.lower()

    async def test_dispatches_delegate_async(self, agent_config):
        """Delegate is a native async executor, dispatched without to_thread."""
        from unittest.mock import AsyncMock, patch
        from src.llm import LLMResponse

        mock_response = LLMResponse(text="sub-agent result", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await execute_tool_call("delegate", {"task": "say hello"}, agent_config)
        assert result == "sub-agent result"

    async def test_dispatches_to_audio_async_with_attachments(self, tmp_path, agent_config):
        """to_audio is dispatched as native async and receives the attachments list."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from src.llm import LLMResponse

        agent_config.attachment_dir = str(tmp_path / "att")
        rewrite = LLMResponse(text="Spoken script.", tool_calls=None)
        resp = MagicMock()
        resp.content = b"BYTES"
        client = MagicMock()
        client.audio.speech.create = AsyncMock(return_value=resp)
        attachments: list[dict] = []

        with patch(
            "src.tools.to_audio.call_llm", new_callable=AsyncMock, return_value=rewrite
        ), patch("src.tools.to_audio.AsyncOpenAI", return_value=client):
            result = await execute_tool_call(
                "to_audio",
                {"content": "hello", "filename": "x.mp3"},
                agent_config,
                attachments=attachments,
            )

        assert "x.mp3" in result
        assert len(attachments) == 1
        assert attachments[0]["mime_type"] == "audio/mpeg"
