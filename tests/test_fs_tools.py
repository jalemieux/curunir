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
