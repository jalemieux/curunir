from src.tools.bash_tool import exec_bash


class TestExecBash:
    def test_simple_command(self, agent_config):
        result = exec_bash({"command": "echo hello"}, agent_config)
        assert "hello" in result

    def test_captures_stderr(self, agent_config):
        result = exec_bash({"command": "echo err >&2"}, agent_config)
        assert "err" in result

    def test_timeout(self, agent_config):
        result = exec_bash({"command": "sleep 10", "timeout": 1}, agent_config)
        assert "timeout" in result.lower() or "timed out" in result.lower()

    def test_nonzero_exit(self, agent_config):
        result = exec_bash({"command": "exit 1"}, agent_config)
        assert isinstance(result, str)
