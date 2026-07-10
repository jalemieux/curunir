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


class TestReadGate:
    """Large-document pre-gate on exec_read (docs/document-ingestion.md step 3)."""

    def _cfg(self, agent_config, gate=200):
        import dataclasses
        return dataclasses.replace(agent_config, read_gate_bytes=gate)

    def _big_file(self, tmp_path, lines=100):
        f = tmp_path / "big.txt"
        f.write_text("\n".join(f"row-{i:04d}" for i in range(1, lines + 1)))
        return f

    def test_gated_read_returns_structural_preview(self, tmp_path, agent_config):
        f = self._big_file(tmp_path)
        result = exec_read({"file_path": str(f)}, self._cfg(agent_config))
        assert "1\trow-0001" in result          # head preview, numbered
        assert "row-0100" not in result          # tail withheld
        assert "100 lines" in result             # totals for offset planning
        assert "offset" in result and "limit" in result
        assert "document-ingest" in result       # routes to the card CLI

    def test_gated_read_returns_card_when_present(self, tmp_path, agent_config):
        f = self._big_file(tmp_path)
        (tmp_path / "big.txt.card.md").write_text("# Document card: big.txt")
        result = exec_read({"file_path": str(f)}, self._cfg(agent_config))
        assert "# Document card: big.txt" in result
        assert str(f) in result                  # points back at the raw file
        assert "row-0001" not in result          # body not inlined

    def test_explicit_limit_bypasses_gate(self, tmp_path, agent_config):
        f = self._big_file(tmp_path)
        result = exec_read(
            {"file_path": str(f), "offset": 98, "limit": 3}, self._cfg(agent_config)
        )
        assert "98\trow-0098" in result
        assert "100\trow-0100" in result
        assert "document-ingest" not in result

    def test_small_file_not_gated(self, tmp_path, agent_config):
        f = tmp_path / "small.txt"
        f.write_text("a\nb\n")
        result = exec_read({"file_path": str(f)}, self._cfg(agent_config))
        assert "1\ta" in result and "2\tb" in result
        assert "document-ingest" not in result

    def test_gate_disabled_when_zero(self, tmp_path, agent_config):
        f = self._big_file(tmp_path)
        result = exec_read({"file_path": str(f)}, self._cfg(agent_config, gate=0))
        assert "row-0100" in result              # full read

    def test_empty_card_falls_back_to_preview(self, tmp_path, agent_config):
        f = self._big_file(tmp_path)
        (tmp_path / "big.txt.card.md").write_text("  \n")
        result = exec_read({"file_path": str(f)}, self._cfg(agent_config))
        assert "1\trow-0001" in result
        assert "document-ingest" in result

    def test_binary_reader_output_is_numbered_and_pageable(self, tmp_path, agent_config):
        # CSV goes through _BINARY_READERS like PDF/DOCX; extracted text must
        # be line-numbered so document-card line refs are read-addressable.
        f = tmp_path / "data.csv"
        f.write_text("\n".join(f"a{i},b{i}" for i in range(1, 11)))
        result = exec_read({"file_path": str(f), "offset": 2, "limit": 2}, agent_config)
        assert "2\ta2\tb2" in result
        assert "3\ta3\tb3" in result
        assert "1\ta1" not in result

    def test_pdf_read_extracts_numbered_text(self, tmp_path, agent_config):
        # Minimal one-page PDF assembled with a correct xref table.
        # Guards the pypdf-based reader: exec_read on a PDF must return
        # extracted text, line-numbered, not an import error.
        stream = b"BT /F1 12 Tf 72 720 Td (Ingestion smoke line) Tj ET"
        objs = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        out, offsets = bytearray(b"%PDF-1.4\n"), []
        for i, body in enumerate(objs, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
        xref_at = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        out += (
            b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref_at)
        )
        f = tmp_path / "doc.pdf"
        f.write_bytes(bytes(out))
        result = exec_read({"file_path": str(f)}, agent_config)
        assert "error" not in result.lower()
        assert "Ingestion smoke line" in result
        assert "\t" in result  # line-numbered like every other format
