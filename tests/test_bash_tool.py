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
        # A command that fails with no output must surface a marker rather than
        # the bare "" that is indistinguishable from a successful no-output run.
        result = exec_bash({"command": "exit 7"}, agent_config)
        assert isinstance(result, str)
        assert "status 7" in result

    def test_silent_failure_surfaces_marker(self, agent_config):
        # Reproduces #413: a verification CLI that can't be found, with stderr
        # discarded, used to return "" and "pass" the gate silently. It must
        # now return a non-empty failure marker.
        result = exec_bash(
            {"command": "python skills/does-not-exist.py 2>/dev/null"},
            agent_config,
        )
        assert result != ""
        assert "no output" in result.lower()
        assert "status" in result.lower()

    def test_nonzero_exit_with_output_unchanged(self, agent_config):
        # A non-zero exit that DOES produce output (e.g. grep-style usage) must
        # surface that output unchanged — no marker, no regression.
        result = exec_bash(
            {"command": "echo found; exit 1"},
            agent_config,
        )
        assert "found" in result
        assert "no output" not in result.lower()

    def test_runs_from_repo_root(self, agent_config, tmp_path, monkeypatch):
        # The subprocess cwd is pinned to config.repo_root, so a repo-relative
        # command succeeds regardless of where the test process is running.
        monkeypatch.chdir(tmp_path)
        result = exec_bash({"command": "ls skills"}, agent_config)
        assert "no output" not in result.lower()
        assert "fact-checker" in result

    def test_repo_root_pwd(self, agent_config, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = exec_bash({"command": "pwd"}, agent_config)
        assert str(agent_config.repo_root) in result


class TestScriptEnv:
    """The bash tool exports the agent's dirs so skill scripts default to them."""

    def test_exports_context_and_shared_dirs(self, tmp_path):
        from src.config import AgentConfig

        ctx = tmp_path / "agents" / "fin"
        cfg = AgentConfig.for_agent("fin", ctx, tmp_path)
        out = exec_bash({"command": 'echo "$CURUNIR_CONTEXT_DIR|$CURUNIR_SHARED_DIR"'}, cfg)
        assert out.strip() == f"{ctx.resolve()}|{tmp_path.resolve()}"

    def test_legacy_config_exports_repo_context_dir(self, agent_config):
        from src.config import AgentConfig

        cfg = AgentConfig()  # legacy layout: both are ./context under repo_root
        out = exec_bash({"command": 'echo "$CURUNIR_CONTEXT_DIR|$CURUNIR_SHARED_DIR"'}, cfg)
        expected = str((cfg.repo_root / "context").resolve())
        assert out.strip() == f"{expected}|{expected}"

    def test_process_env_is_inherited(self, agent_config, monkeypatch):
        monkeypatch.setenv("CURUNIR_TEST_MARKER", "present")
        out = exec_bash({"command": "echo $CURUNIR_TEST_MARKER"}, agent_config)
        assert out.strip() == "present"
