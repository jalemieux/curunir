# src/llm.py
import asyncio
import logging
import time
from dataclasses import dataclass, field

import litellm

litellm.suppress_debug_info = True

log = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_BASE_DELAY = 2  # seconds


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    elapsed_sec: float = 0.0

    @property
    def completion_tps(self) -> float:
        """Completion tokens per second."""
        return self.completion_tokens / self.elapsed_sec if self.elapsed_sec > 0 else 0.0


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[dict] | None
    usage: LLMUsage = field(default_factory=LLMUsage)


async def call_llm(
    model: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int = 16_000,
    api_base: str | None = None,
    openrouter_provider: str | None = None,
) -> LLMResponse:
    """Call LLM via LiteLLM, return normalized response."""
    kwargs = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "num_retries": 0,  # disable LiteLLM's internal retries; we handle retries below
    }
    if api_base:
        kwargs["api_base"] = api_base
    if openrouter_provider:
        kwargs["extra_body"] = {"provider": {"order": [openrouter_provider]}}
    if tools:
        kwargs["tools"] = tools

    import json as _json
    log.info("LLM_REQUEST_DEBUG model=%s n_msgs=%d", model, len(messages))
    for idx, m in enumerate(messages):
        role = m.get("role")
        has_tool_calls = "tool_calls" in m
        content = m.get("content")
        content_preview = (content[:300] if isinstance(content, str) else _json.dumps(content)[:300]) if content is not None else "<none>"
        log.info("  [%d] role=%s tool_calls=%s content=%r", idx, role, has_tool_calls, content_preview)

    t0 = time.monotonic()
    for attempt in range(MAX_RETRIES):
        try:
            response = await litellm.acompletion(**kwargs)
            break
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status in (429, 502, 503) and attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                log.warning("LLM returned %s, retrying in %ss (attempt %d/%d)",
                            status, delay, attempt + 1, MAX_RETRIES)
                await asyncio.sleep(delay)
            else:
                log.error("LLM_REQUEST_FAILED model=%s err=%s", model, exc)
                log.error("LLM_REQUEST_FAILED full_messages=%s", _json.dumps(messages, default=str)[:4000])
                raise
    elapsed = time.monotonic() - t0

    choice = response.choices[0].message

    # Extract usage stats
    usage = LLMUsage(elapsed_sec=elapsed)
    if response.usage:
        usage.prompt_tokens = response.usage.prompt_tokens or 0
        usage.completion_tokens = response.usage.completion_tokens or 0
        usage.total_tokens = response.usage.total_tokens or 0

    text = choice.content if choice.content else None

    tool_calls = None
    if choice.tool_calls:
        tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in choice.tool_calls
        ]

    return LLMResponse(text=text, tool_calls=tool_calls, usage=usage)
