import os

import pytest

from src.config import AgentConfig
from src.tools.fs_tools import exec_glob, exec_grep, exec_read, exec_edit, exec_write


class TestExecGlob:
    def test_finds_files(self, tmp_path, agent_config):
        (tmp_path / "a.py").write_text("hello")
        (tmp_path / "b.txt").write_text("world")
        result = exec_glob({"pattern": "*.py", "path": str(tmp_path)}, agent_config)
        assert "a.py" in result
        assert "b.txt" not in result

    def test_recursive(self, tmp_path, agent_config):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("x")
        result = exec_glob({"pattern": "**/*.py", "path": str(tmp_path)}, agent_config)
        assert "deep.py" in result

    def test_no_matches(self, tmp_path, agent_config):
        result = exec_glob({"pattern": "*.xyz", "path": str(tmp_path)}, agent_config)
        assert result == ""

    def test_default_path(self, agent_config):
        result = exec_glob({"pattern": "*.py"}, agent_config)
        assert isinstance(result, str)

    def test_absolute_pattern_stripped(self, tmp_path, agent_config):
        """Absolute patterns like /**/*.py must be made relative to root_dir."""
        (tmp_path / "a.py").write_text("x")
        result = exec_glob({"pattern": "/**/*.py", "path": str(tmp_path)}, agent_config)
        # Should find the file relative to tmp_path, not scan from /
        assert "a.py" in result

    def test_slash_only_pattern(self, agent_config):
        result = exec_glob({"pattern": "/"}, agent_config)
        assert "error" in result.lower()


class TestExecRead:
    def test_reads_file_with_line_numbers(self, tmp_path, agent_config):
        f = tmp_path / "test.txt"
        f.write_text("line one\nline two\nline three\n")
        result = exec_read({"file_path": str(f)}, agent_config)
        assert "1\tline one" in result
        assert "2\tline two" in result
        assert "3\tline three" in result

    def test_offset_and_limit(self, tmp_path, agent_config):
        f = tmp_path / "test.txt"
        f.write_text("a\nb\nc\nd\ne\n")
        result = exec_read({"file_path": str(f), "offset": 2, "limit": 2}, agent_config)
        assert "1\t" not in result
        assert "2\tb" in result
        assert "3\tc" in result
        assert "4\t" not in result

    def test_file_not_found(self, agent_config):
        result = exec_read({"file_path": "/nonexistent/file.txt"}, agent_config)
        assert "error" in result.lower()


class TestExecEdit:
    def test_replaces_unique_string(self, tmp_path, agent_config):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = exec_edit(
            {"file_path": str(f), "old_string": "hello", "new_string": "goodbye"},
            agent_config,
        )
        assert f.read_text() == "goodbye world"
        assert "ok" in result.lower() or "success" in result.lower() or "replaced" in result.lower()

    def test_fails_if_not_found(self, tmp_path, agent_config):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = exec_edit(
            {"file_path": str(f), "old_string": "xyz", "new_string": "abc"},
            agent_config,
        )
        assert "not found" in result.lower()

    def test_fails_if_not_unique(self, tmp_path, agent_config):
        f = tmp_path / "test.txt"
        f.write_text("aa bb aa")
        result = exec_edit(
            {"file_path": str(f), "old_string": "aa", "new_string": "cc"},
            agent_config,
        )
        assert "not unique" in result.lower() or "multiple" in result.lower()
        assert f.read_text() == "aa bb aa"  # unchanged

    def test_replace_all(self, tmp_path, agent_config):
        f = tmp_path / "test.txt"
        f.write_text("aa bb aa")
        result = exec_edit(
            {"file_path": str(f), "old_string": "aa", "new_string": "cc", "replace_all": True},
            agent_config,
        )
        assert f.read_text() == "cc bb cc"


class TestExecWrite:
    def test_creates_file(self, tmp_path, agent_config):
        f = tmp_path / "new.txt"
        result = exec_write({"file_path": str(f), "content": "hello"}, agent_config)
        assert f.read_text() == "hello"

    def test_creates_parent_dirs(self, tmp_path, agent_config):
        f = tmp_path / "a" / "b" / "new.txt"
        exec_write({"file_path": str(f), "content": "deep"}, agent_config)
        assert f.read_text() == "deep"

    def test_overwrites_existing(self, tmp_path, agent_config):
        f = tmp_path / "existing.txt"
        f.write_text("old")
        exec_write({"file_path": str(f), "content": "new"}, agent_config)
        assert f.read_text() == "new"


class TestExecGrep:
    def test_finds_pattern(self, tmp_path, agent_config):
        (tmp_path / "a.py").write_text("def hello():\n    pass\n")
        result = exec_grep({"pattern": "def hello", "path": str(tmp_path)}, agent_config)
        assert "def hello" in result

    def test_no_matches(self, tmp_path, agent_config):
        (tmp_path / "a.py").write_text("nothing here\n")
        result = exec_grep({"pattern": "zzzzz", "path": str(tmp_path)}, agent_config)
        assert result == "" or "no matches" in result.lower()


# ---------------------------------------------------------------------------
# Cross-persona filesystem isolation (the headline #420 requirement).
#
# Each persona's FS tools are confined to its own workdir (the persona's
# container/namespace mount root in production; a hardened realpath path-jail
# here as the enforced, testable defense-in-depth layer). Persona A must not
# be able to read/write/glob/grep persona B's tree via relative paths, ``..``,
# absolute paths, or symlinks.
# ---------------------------------------------------------------------------
class TestCrossPersonaIsolation:
    @pytest.fixture
    def two_personas(self, tmp_path):
        """Two jailed personas under a shared context root, each with a secret."""
        ctx = tmp_path / "context"
        a_ctx = ctx / "alpha"
        b_ctx = ctx / "bravo"
        a_work = a_ctx / "workspace"
        b_work = b_ctx / "workspace"
        for d in (a_work, b_work):
            d.mkdir(parents=True)
        # B's secret lives OUTSIDE its own workspace too (whole tree is private).
        (b_work / "secret.txt").write_text("bravo-private-data")
        (b_ctx / "memory").mkdir()
        (b_ctx / "memory" / "facts.md").write_text("bravo-memory")
        (a_work / "mine.txt").write_text("alpha-own-data")
        cfg_a = AgentConfig(context_dir=a_ctx, fs_jail=True)
        cfg_b = AgentConfig(context_dir=b_ctx, fs_jail=True)
        return cfg_a, cfg_b, a_work, b_work, b_ctx

    def test_persona_can_reach_own_files(self, two_personas):
        cfg_a, _, a_work, _, _ = two_personas
        # Relative to the jail root.
        assert "alpha-own-data" in exec_read({"file_path": "mine.txt"}, cfg_a)
        # Absolute path that stays inside the jail is fine.
        assert "alpha-own-data" in exec_read(
            {"file_path": str(a_work / "mine.txt")}, cfg_a
        )

    def test_read_blocked_via_absolute_path(self, two_personas):
        cfg_a, _, _, b_work, _ = two_personas
        result = exec_read({"file_path": str(b_work / "secret.txt")}, cfg_a)
        assert "bravo-private-data" not in result
        assert "error" in result.lower()

    def test_read_blocked_via_dotdot(self, two_personas):
        cfg_a, _, _, _, b_ctx = two_personas
        # ../../bravo/workspace/secret.txt from alpha/workspace
        rel = os.path.join("..", "..", "bravo", "workspace", "secret.txt")
        result = exec_read({"file_path": rel}, cfg_a)
        assert "bravo-private-data" not in result
        assert "error" in result.lower()

    def test_read_blocked_via_symlink_escape(self, two_personas):
        cfg_a, _, a_work, _, b_ctx = two_personas
        # Plant a symlink inside alpha's jail pointing at bravo's tree.
        link = a_work / "escape"
        link.symlink_to(b_ctx)
        result = exec_read({"file_path": "escape/workspace/secret.txt"}, cfg_a)
        assert "bravo-private-data" not in result
        assert "error" in result.lower()

    def test_write_blocked_outside_jail(self, two_personas):
        cfg_a, _, _, b_work, _ = two_personas
        target = b_work / "clobbered.txt"
        result = exec_write({"file_path": str(target), "content": "x"}, cfg_a)
        assert "error" in result.lower()
        assert not target.exists()

    def test_edit_blocked_outside_jail(self, two_personas):
        cfg_a, _, _, b_work, _ = two_personas
        result = exec_edit(
            {"file_path": str(b_work / "secret.txt"),
             "old_string": "bravo", "new_string": "hacked"},
            cfg_a,
        )
        assert "error" in result.lower()
        assert (b_work / "secret.txt").read_text() == "bravo-private-data"

    def test_glob_confined_to_jail(self, two_personas):
        cfg_a, _, _, _, b_ctx = two_personas
        # Try to glob bravo's tree by absolute path.
        result = exec_glob({"pattern": "**/*.txt", "path": str(b_ctx)}, cfg_a)
        assert "secret.txt" not in result
        assert "error" in result.lower()

    def test_grep_confined_to_jail(self, two_personas):
        cfg_a, _, _, b_work, _ = two_personas
        result = exec_grep(
            {"pattern": "private", "path": str(b_work)}, cfg_a
        )
        assert "bravo-private-data" not in result
        assert "error" in result.lower()
