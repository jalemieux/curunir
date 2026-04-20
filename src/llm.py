# src/llm.py
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
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


async def _consume_stream(
    response_iter,
    on_text_delta: Callable[[str], Awaitable[None]],
) -> tuple[str, dict[int, dict], LLMUsage]:
    """Drain a LiteLLM streaming response.

    Returns (full_text, tool_calls_by_index, usage). tool_calls_by_index maps
    the delta `index` to a dict with keys: id, name, arguments (concatenated).
    """
    text_parts: list[str] = []
    tc_by_index: dict[int, dict] = {}
    usage = LLMUsage()

    async for chunk in response_iter:
        if getattr(chunk, "usage", None):
            usage.prompt_tokens = chunk.usage.prompt_tokens or 0
            usage.completion_tokens = chunk.usage.completion_tokens or 0
            usage.total_tokens = chunk.usage.total_tokens or 0

        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        text_piece = getattr(delta, "content", None)
        if text_piece:
            text_parts.append(text_piece)
            await on_text_delta(text_piece)

        delta_tcs = getattr(delta, "tool_calls", None) or []
        for tc in delta_tcs:
            idx = tc.index
            entry = tc_by_index.setdefault(
                idx, {"id": None, "name": None, "arguments": ""}
            )
            if getattr(tc, "id", None):
                entry["id"] = tc.id
            fn = getattr(tc, "function", None)
            if fn is not None:
                if getattr(fn, "name", None):
                    entry["name"] = fn.name
                if getattr(fn, "arguments", None):
                    entry["arguments"] += fn.arguments

    return "".join(text_parts), tc_by_index, usage


async def call_llm(
    model: str,
    messages: list[dict],
    tools: list[dict],
    api_base: str | None = None,
    openrouter_provider: str | None = None,
    on_text_delta: Callable[[str], Awaitable[None]] | None = None,
) -> LLMResponse:
    """Call LLM via LiteLLM, return normalized response.

    When ``on_text_delta`` is provided, switches to streaming mode and fires
    the callback for each text chunk. Tool calls and usage are still returned
    as a complete ``LLMResponse``.
    """
    kwargs = {
        "model": model,
        "messages": messages,
        "max_tokens": 16000,
        "num_retries": 0,  # disable LiteLLM's internal retries; we handle retries below
    }
    if api_base:
        kwargs["api_base"] = api_base
    if openrouter_provider:
        kwargs["extra_body"] = {"provider": {"order": [openrouter_provider]}}
    if tools:
        kwargs["tools"] = tools

    streaming = on_text_delta is not None
    if streaming:
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

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
                raise

    if streaming:
        text, tc_by_index, usage = await _consume_stream(response, on_text_delta)
        usage.elapsed_sec = time.monotonic() - t0

        tool_calls: list[dict] | None = None
        if tc_by_index:
            tool_calls = [
                {
                    "id": entry["id"],
                    "type": "function",
                    "function": {
                        "name": entry["name"],
                        "arguments": entry["arguments"],
                    },
                }
                for _, entry in sorted(tc_by_index.items())
            ]

        return LLMResponse(text=text or None, tool_calls=tool_calls, usage=usage)

    elapsed = time.monotonic() - t0
    choice = response.choices[0].message

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
