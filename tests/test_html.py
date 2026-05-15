"""Tests for src/channels/_html.py — markdown → HTML rendering for email replies."""

from src.channels._html import render_markdown


def test_renders_heading_h1():
    html = render_markdown("# Hello world")
    assert "<h1>Hello world</h1>" in html


def test_renders_heading_h2():
    html = render_markdown("## Section")
    assert "<h2>Section</h2>" in html


def test_renders_bulleted_list():
    html = render_markdown("- one\n- two\n- three")
    assert "<ul>" in html
    assert "<li>one</li>" in html
    assert "<li>two</li>" in html
    assert "<li>three</li>" in html


def test_renders_inline_bold():
    html = render_markdown("This is **bold** text")
    assert "<strong>bold</strong>" in html


def test_renders_fenced_code_block():
    md = "```\nprint('hi')\n```"
    html = render_markdown(md)
    assert "<pre" in html
    assert "<code" in html
    assert "print(&#39;hi&#39;)" in html or "print('hi')" in html


def test_renders_inline_link():
    html = render_markdown("See [docs](https://example.com/x).")
    assert 'href="https://example.com/x"' in html
    assert ">docs</a>" in html


def test_escapes_raw_html_in_source():
    # html=False should escape (not pass through) raw HTML so a malicious or
    # accidental <script> in the agent reply never ends up live in the email.
    html = render_markdown("hello <script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_wraps_in_html_skeleton():
    html = render_markdown("hi")
    # Lowercase or uppercase doctype, both fine — just assert presence.
    assert html.lower().startswith("<!doctype html>")
    assert "<body" in html
    assert "</body>" in html
    assert "</html>" in html


def test_body_has_inline_styling():
    html = render_markdown("hi")
    # Inline font/color styling on <body> — clients strip <style> blocks.
    assert "font-family" in html
    # No top-level <style> block — we rely on inline styles only.
    assert "<style" not in html.lower()


def test_link_has_inline_styling():
    html = render_markdown("[here](https://example.com)")
    # The opening <a> tag should carry an inline style so Gmail/Apple Mail
    # render it as a recognizable link without needing CSS rules.
    import re
    m = re.search(r"<a [^>]*href=\"https://example.com\"[^>]*>", html)
    assert m is not None
    assert "style=" in m.group(0)


def test_code_block_has_inline_styling():
    html = render_markdown("```\nx = 1\n```")
    # Either <pre> or <code> must have an inline style so monospace + background
    # render in clients that drop <style> blocks.
    import re
    pre_tag = re.search(r"<pre[^>]*>", html)
    code_tag = re.search(r"<code[^>]*>", html)
    assert pre_tag is not None
    assert code_tag is not None
    assert "style=" in pre_tag.group(0) or "style=" in code_tag.group(0)
