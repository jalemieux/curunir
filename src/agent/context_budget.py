"""Resolve the llama.cpp context window at startup.

When `api_base` is set, query `/slots` once to read the model's
real `n_ctx`, store it on `AgentConfig`, and clamp `max_tokens`
to half the window. The agent loop then drives all trim decisions
off the per-call `response.usage.prompt_tokens`.

For hosted models (no `api_base`), this module is never called —
`n_ctx` stays `None` and the agent relies on
`ContextWindowExceededError` for reactive trim only.
"""
import logging
from urllib.parse import urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)


async def _fetch_n_ctx(api_base: str) -> int:
    """GET {api_base root}/slots and return n_ctx from the first slot.

    llama.cpp's /slots endpoint returns a list of slot dicts, each with n_ctx.
    All slots share the same context window.
    """
    parsed = urlparse(api_base)
    url = urlunparse(parsed._replace(path="/slots"))
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        slots = resp.json()
    if not slots or "n_ctx" not in slots[0]:
        raise RuntimeError(f"llama.cpp /slots response missing n_ctx: {slots}")
    return int(slots[0]["n_ctx"])


async def resolve_llamacpp_budget(config) -> None:
    """Mutate config.n_ctx and config.max_tokens in place.

    Only call when config.api_base is set. Aborts with RuntimeError on
    HTTP error or missing n_ctx field.
    """
    n_ctx = await _fetch_n_ctx(config.api_base)
    config.n_ctx = n_ctx
    config.max_tokens = min(config.max_tokens, n_ctx // 2)
    logger.info(
        "llama.cpp context: n_ctx=%d, max_tokens=%d",
        n_ctx, config.max_tokens,
    )
