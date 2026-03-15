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

    async def test_dispatches_delegate_async(self, agent_config):
        """Delegate is a native async executor, dispatched without to_thread."""
        from unittest.mock import AsyncMock, patch
        from src.llm import LLMResponse

        mock_response = LLMResponse(text="sub-agent result", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await execute_tool_call("delegate", {"task": "say hello"}, agent_config)
        assert result == "sub-agent result"
