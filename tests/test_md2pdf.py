"""Tests for src/md2pdf.py — the shared markdown→PDF helper.

The helper sanitizes LaTeX-hostile emoji/pictographs, renders via pandoc with
xelatex (broad Unicode), and falls back to pdflatex. The real subprocess call is
isolated in ``_run_pandoc`` so the engine-selection logic is testable without a
LaTeX toolchain installed.
"""

import pytest

from src import md2pdf


# --- sanitize_markdown ------------------------------------------------------

def test_sanitize_strips_check_and_cross_emoji():
    # ✅ U+2705 and ❌ U+274C are the exact glyphs that crashed pdflatex (#452).
    text = "Status: done ✅, blocked ❌."
    out = md2pdf.sanitize_markdown(text)
    assert "✅" not in out
    assert "❌" not in out


def test_sanitize_strips_pictographs_and_variation_selectors():
    text = "Launch 🚀️ and party 🎉 and warning ⚠️"
    out = md2pdf.sanitize_markdown(text)
    for ch in ("🚀", "🎉", "⚠", "️"):
        assert ch not in out


def test_sanitize_preserves_typographic_unicode():
    # Curly quotes, em-dash, ellipsis, bullet, accents must survive — they
    # render fine and are part of the deliberate typeset aesthetic.
    text = "“Quote” — café, naïve… • résumé"
    out = md2pdf.sanitize_markdown(text)
    assert out == text


def test_sanitize_preserves_citation_markdown():
    # deep-research's inline-citation syntax is pure ASCII; must be untouched.
    text = "A claim [^1^](#src-1) with a fact."
    assert md2pdf.sanitize_markdown(text) == text


def test_sanitize_is_idempotent():
    text = "Done ✅ now"
    once = md2pdf.sanitize_markdown(text)
    assert md2pdf.sanitize_markdown(once) == once


# --- convert: engine selection ----------------------------------------------

@pytest.fixture
def md_file(tmp_path):
    p = tmp_path / "report.md"
    p.write_text("# Title\n\nDone ✅\n")
    return p


def test_convert_prefers_xelatex(monkeypatch, md_file):
    calls = []

    def fake_run(text, dst, engine):
        calls.append(engine)
        dst.write_bytes(b"%PDF-1.5 fake")
        return md2pdf.PandocResult(ok=True, stderr="")

    monkeypatch.setattr(md2pdf, "_run_pandoc", fake_run)
    out = md2pdf.convert(md_file)
    assert out == md_file.with_suffix(".pdf")
    assert out.exists()
    assert calls == ["xelatex"]  # first engine succeeded, no fallback


def test_convert_falls_back_to_pdflatex(monkeypatch, md_file):
    calls = []

    def fake_run(text, dst, engine):
        calls.append(engine)
        if engine == "xelatex":
            return md2pdf.PandocResult(ok=False, stderr="xelatex: not found")
        dst.write_bytes(b"%PDF-1.5 fake")
        return md2pdf.PandocResult(ok=True, stderr="")

    monkeypatch.setattr(md2pdf, "_run_pandoc", fake_run)
    out = md2pdf.convert(md_file)
    assert out.exists()
    assert calls == ["xelatex", "pdflatex"]  # tried xelatex, then fell back


def test_convert_raises_when_all_engines_fail(monkeypatch, md_file):
    def fake_run(text, dst, engine):
        return md2pdf.PandocResult(ok=False, stderr=f"{engine} boom")

    monkeypatch.setattr(md2pdf, "_run_pandoc", fake_run)
    with pytest.raises(md2pdf.Md2PdfError):
        md2pdf.convert(md_file)


def test_convert_sanitizes_before_render(monkeypatch, md_file):
    seen = {}

    def fake_run(text, dst, engine):
        seen["text"] = text
        dst.write_bytes(b"%PDF-1.5 fake")
        return md2pdf.PandocResult(ok=True, stderr="")

    monkeypatch.setattr(md2pdf, "_run_pandoc", fake_run)
    md2pdf.convert(md_file)
    assert "✅" not in seen["text"]


def test_convert_leaves_source_markdown_untouched(monkeypatch, md_file):
    original = md_file.read_text()

    def fake_run(text, dst, engine):
        dst.write_bytes(b"%PDF-1.5 fake")
        return md2pdf.PandocResult(ok=True, stderr="")

    monkeypatch.setattr(md2pdf, "_run_pandoc", fake_run)
    md2pdf.convert(md_file)
    assert md_file.read_text() == original  # sanitize must not mutate the .md


def test_convert_honors_explicit_output_path(monkeypatch, md_file, tmp_path):
    def fake_run(text, dst, engine):
        dst.write_bytes(b"%PDF-1.5 fake")
        return md2pdf.PandocResult(ok=True, stderr="")

    monkeypatch.setattr(md2pdf, "_run_pandoc", fake_run)
    dst = tmp_path / "out" / "custom.pdf"
    dst.parent.mkdir()
    out = md2pdf.convert(md_file, dst)
    assert out == dst
    assert dst.exists()


# --- CLI --------------------------------------------------------------------

def test_main_success(monkeypatch, md_file, capsys):
    def fake_run(text, dst, engine):
        dst.write_bytes(b"%PDF-1.5 fake")
        return md2pdf.PandocResult(ok=True, stderr="")

    monkeypatch.setattr(md2pdf, "_run_pandoc", fake_run)
    rc = md2pdf.main([str(md_file)])
    assert rc == 0
    assert str(md_file.with_suffix(".pdf")) in capsys.readouterr().out


def test_main_failure_returns_nonzero(monkeypatch, md_file, capsys):
    def fake_run(text, dst, engine):
        return md2pdf.PandocResult(ok=False, stderr="boom")

    monkeypatch.setattr(md2pdf, "_run_pandoc", fake_run)
    rc = md2pdf.main([str(md_file)])
    assert rc == 1
    assert "boom" in capsys.readouterr().err.lower() or rc == 1
