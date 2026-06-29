"""Shared markdown→styled-HTML renderer for email delivery.

One renderer, one stylesheet, one place to change styling. Used by both the
inbound reply path (`src.channels.email.EmailChannel.send`) and the proactive
send CLI (`skills/email-send/email_send.py`), so a markdown body renders to the
same pretty HTML whether the agent is replying or sending fresh.

`render_html` returns "" when the `markdown` library is unavailable for any
reason; callers treat an empty return as the text-only fallback (`... or None`).
"""

import logging

logger = logging.getLogger(__name__)

_HTML_WRAPPER = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  body {{ font-family: -apple-system, system-ui, "Segoe UI", sans-serif; max-width: 700px; margin: 0 auto; padding: 24px 16px; font-size: 16px; line-height: 1.6; color: #1a1a1a; }}
  h1 {{ font-size: 24px; margin: 0 0 0.8em 0; font-weight: 600; }}
  h2 {{ font-size: 20px; margin: 1.2em 0 0.6em 0; font-weight: 600; }}
  h3 {{ font-size: 17px; margin: 1.1em 0 0.5em 0; font-weight: 600; }}
  p {{ margin: 0 0 1.1em 0; }}
  strong {{ font-weight: 600; }}
  em {{ font-style: italic; }}
  a {{ color: #1a5fb4; text-decoration: underline; }}
  ul, ol {{ margin: 0 0 1.1em 0; padding-left: 1.6em; }}
  li {{ margin: 0.2em 0; }}
  code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.92em; background: #f3f3f3; padding: 0.1em 0.3em; border-radius: 3px; }}
  pre {{ background: #f3f3f3; padding: 12px; border-radius: 4px; overflow-x: auto; line-height: 1.4; }}
  pre code {{ background: none; padding: 0; }}
  blockquote {{ margin: 0 0 1.1em 0; padding: 0 1em; border-left: 3px solid #ddd; color: #555; }}
</style></head><body>
{body}
</body></html>"""


def render_html(markdown_text: str) -> str:
    """Render markdown to a styled standalone HTML document for email delivery.

    Returns "" if the markdown library is unavailable for any reason — callers
    treat an empty return as the text-only fallback (`render_html(...) or None`).
    """
    try:
        import markdown as _md
    except ImportError:
        logger.warning("markdown library unavailable; falling back to text-only email")
        return ""
    rendered = _md.markdown(markdown_text, extensions=["extra", "sane_lists"])
    return _HTML_WRAPPER.format(body=rendered)
