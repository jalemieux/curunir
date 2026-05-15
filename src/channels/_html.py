"""Markdown → HTML renderer for the HTML half of multipart email replies.

The agent emits markdown. Most mail clients render multipart/alternative;
this module produces the HTML body. We stick to inline styling on the
elements we emit because Gmail, Apple Mail, and Outlook all drop or
sandbox top-level ``<style>`` blocks.
"""
from __future__ import annotations

from markdown_it import MarkdownIt

_BODY_STYLE = (
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
    "Helvetica,Arial,sans-serif;"
    "font-size:14px;line-height:1.5;color:#222;"
)
_LINK_STYLE = "color:#1a73e8;text-decoration:underline;"
_PRE_STYLE = (
    "background:#f4f4f4;border-radius:4px;padding:8px 12px;"
    "font-family:Menlo,Consolas,monospace;font-size:13px;"
    "overflow:auto;"
)
_CODE_STYLE = "font-family:Menlo,Consolas,monospace;font-size:13px;"

_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": False})


def render_markdown(md: str) -> str:
    """Render a markdown string to a complete HTML document.

    Raw HTML in the source is escaped, not passed through, so the agent
    cannot smuggle a ``<script>`` into the rendered email.
    """
    body = _md.render(md or "")
    body = body.replace("<a ", f'<a style="{_LINK_STYLE}" ')
    body = body.replace("<pre>", f'<pre style="{_PRE_STYLE}">')
    body = body.replace("<code>", f'<code style="{_CODE_STYLE}">')
    return (
        "<!doctype html>"
        "<html><head><meta charset=\"utf-8\"></head>"
        f'<body style="{_BODY_STYLE}">{body}</body></html>'
    )
