"""Resolve the llama.cpp context budget at startup.

When `api_base` is set, query `/slots` once for `n_ctx`, measure the
static system prompt + tool schemas, and derive `max_history_chars`.
For hosted models (no `api_base`), this module is never called —
`max_history_chars` stays `None` and the agent relies on LiteLLM's
`ContextWindowExceededError` for reactive trim.
"""
import json
import logging
from urllib.parse import urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 3
SAFETY_MARGIN = 500


def _compute_history_budget(
    *,
    n_ctx_tokens: int,
    static_prompt: str,
    tool_schemas: list[dict],
    max_tokens: int,
) -> int:
    """Return history budget in chars, given model window + overhead."""
    total = n_ctx_tokens * CHARS_PER_TOKEN
    prompt_chars = len(static_prompt)
    schema_chars = len(json.dumps(tool_schemas))
    response_chars = max_tokens * CHARS_PER_TOKEN
    return total - prompt_chars - schema_chars - response_chars - SAFETY_MARGIN
