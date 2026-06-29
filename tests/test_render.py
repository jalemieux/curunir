"""Tests for the shared email-render module (src/channels/_render.py).

`render_html` is the single markdown→styled-HTML renderer shared by the email
reply path (src/channels/email.py) and the proactive send CLI
(skills/email-send/email_send.py). It returns "" when the `markdown` library is
unavailable so callers can degrade to text-only.
"""
import builtins

import pytest

from src.channels._render import render_html


def test_render_html_produces_wrapped_html():
    """Rich markdown renders to real HTML inside the standalone wrapper."""
    sample = (
        "# Top heading\n\n"
        "Some **bold** and *italic* and a [link](https://example.com).\n\n"
        "- bullet one\n- bullet two\n\n"
        "Inline `code` here.\n"
    )
    html = render_html(sample)

    # Wrapper shell present
    assert html.startswith("<!DOCTYPE html>")
    assert "<style>" in html
    assert "</body></html>" in html

    # Markdown rendered to tags
    assert "<h1>" in html and "Top heading" in html
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert '<a href="https://example.com">link</a>' in html
    assert "<ul>" in html and "<li>bullet one</li>" in html
    assert "<code>code</code>" in html

    # Raw markdown syntax must not survive into the rendered body
    body = html[html.rfind("<body>"):html.rfind("</body>")]
    assert "**" not in body
    assert "[link](" not in body


def test_render_html_returns_empty_on_import_error(monkeypatch):
    """If the markdown library can't import, render returns "" (text-only fallback)."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "markdown":
            raise ImportError("simulated missing markdown")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert render_html("# hi\n\n**bold**") == ""
