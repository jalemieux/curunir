# src/tools/web_fetch.py
import logging

import httpx
from trafilatura import extract

from src.config import AgentConfig

logger = logging.getLogger(__name__)

_MAX_CONTENT_CHARS = 20_000  # ~5k tokens
_TIMEOUT = 30


def exec_web_fetch(args: dict, config: AgentConfig) -> str:
    """Fetch a URL and return extracted text content."""
    url = args.get("url", "")
    if not url:
        return "Error: 'url' is required"

    try:
        resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; research-agent/1.0)",
        })
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return f"Error fetching {url}: {e}"

    text = extract(resp.text, include_links=True, include_tables=True)
    if not text:
        return f"Could not extract readable content from {url}"

    if len(text) > _MAX_CONTENT_CHARS:
        text = text[:_MAX_CONTENT_CHARS] + f"\n\n... truncated ({len(text)} chars total)"

    return text
