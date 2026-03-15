import pytest

from src.tools.dispatcher import execute_tool_call, is_async_executor


class TestAsyncDispatch:
    def test_is_async_executor_false_for_sync(self):
        assert is_async_executor("bash") is False

    def test_is_async_executor_true_for_async(self):
        # Will be true once delegate is registered in Task 3
        assert is_async_executor("delegate") is True


class TestExecuteToolCallAsync:
    async def test_unknown_async_tool_returns_error(self, agent_config):
        from src.tools.dispatcher import execute_tool_call_async
        result = await execute_tool_call_async("nonexistent", {}, agent_config)
        assert "unknown" in result.lower()


class TestExecuteToolCall:
    def test_dispatches_glob(self, tmp_path, agent_config):
        (tmp_path / "x.py").write_text("hi")
        result = execute_tool_call("glob", {"pattern": "*.py", "path": str(tmp_path)}, agent_config)
        assert "x.py" in result

    def test_dispatches_read(self, tmp_path, agent_config):
        f = tmp_path / "test.txt"
        f.write_text("content")
        result = execute_tool_call("read", {"file_path": str(f)}, agent_config)
        assert "content" in result

    def test_dispatches_write(self, tmp_path, agent_config):
        f = tmp_path / "out.txt"
        execute_tool_call("write", {"file_path": str(f), "content": "data"}, agent_config)
        assert f.read_text() == "data"

    def test_dispatches_bash(self, agent_config):
        result = execute_tool_call("bash", {"command": "echo dispatch_test"}, agent_config)
        assert "dispatch_test" in result

    def test_unknown_tool(self, agent_config):
        result = execute_tool_call("nonexistent", {}, agent_config)
        assert "unknown tool" in result.lower()

    def test_case_insensitive(self, agent_config):
        result = execute_tool_call("Bash", {"command": "echo case_test"}, agent_config)
        assert "case_test" in result
