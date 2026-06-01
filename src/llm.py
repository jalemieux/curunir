# src/llm.py
import asyncio
import base64
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import litellm

litellm.suppress_debug_info = True

log = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_BASE_DELAY = 2  # seconds

_QUOTA_MSG = "I've hit my allocated quota — please try again later."
_RATE_LIMIT_MSG = (
    "The LLM provider is rate-limiting me right now. Please try again in a minute."
)
_QUOTA_SUBSTRINGS = ("key limit", "quota", "monthly limit", "insufficient_quota")


def classify_provider_error(exc: Exception) -> tuple[str, str] | None:
    """Classify an LLM-provider exception into a user-facing category.

    Returns ``(category, user_message)`` for known classes, ``None`` for
    unknown ones so the caller can re-raise and preserve existing behaviour.
    Pure detection — no logging, no side effects.
    """
    status = getattr(exc, "status_code", None)
    body = str(exc).lower()

    rate_limit_cls = getattr(litellm, "RateLimitError", None)
    auth_cls = getattr(litellm, "AuthenticationError", None)

    if auth_cls is not None and isinstance(exc, auth_cls):
        return "quota_exhausted", _QUOTA_MSG
    if status == 403 or any(s in body for s in _QUOTA_SUBSTRINGS):
        return "quota_exhausted", _QUOTA_MSG
    if (rate_limit_cls is not None and isinstance(exc, rate_limit_cls)) or status == 429:
        return "rate_limited", _RATE_LIMIT_MSG
    return None

# In-process cache for describe_image, keyed by SHA-256 of the image bytes so
# entries stay valid even when the same logical file is re-staged at a new
# path. Unbounded by design — fine for normal session sizes.
_description_cache: dict[str, str] = {}


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    elapsed_sec: float = 0.0
    model: str = ""
    cached_prompt_tokens: int = 0
    reasoning_tokens: int = 0
    image_tokens: int = 0
    audio_tokens: int = 0
    cost_usd: float | None = None

    @property
    def completion_tps(self) -> float:
        """Completion tokens per second."""
        return self.completion_tokens / self.elapsed_sec if self.elapsed_sec > 0 else 0.0


def _attr(obj, name, default=None):
    """Read a field from a pydantic-like object or a dict, with default."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _int_at(obj, *path: str) -> int:
    """Walk nested fields, returning 0 if any segment is absent or non-numeric."""
    cur = obj
    for p in path:
        cur = _attr(cur, p)
        if cur is None:
            return 0
    try:
        return int(cur)
    except (TypeError, ValueError):
        return 0


def _populate_usage_dims(usage: LLMUsage, raw_usage, response=None) -> None:
    """Fill OpenRouter-aligned billing dimensions from a raw usage object.

    Probes both OpenAI/OpenRouter shapes (prompt_tokens_details.cached_tokens,
    completion_tokens_details.reasoning_tokens) and Anthropic-direct shapes
    (cache_read_input_tokens). Falls back to 0 when fields are absent.
    """
    if raw_usage is None:
        return
    usage.cached_prompt_tokens = (
        _int_at(raw_usage, "prompt_tokens_details", "cached_tokens")
        or _int_at(raw_usage, "cache_read_input_tokens")
    )
    usage.reasoning_tokens = _int_at(raw_usage, "completion_tokens_details", "reasoning_tokens")
    usage.image_tokens = _int_at(raw_usage, "prompt_tokens_details", "image_tokens")
    usage.audio_tokens = (
        _int_at(raw_usage, "prompt_tokens_details", "audio_tokens")
        or _int_at(raw_usage, "completion_tokens_details", "audio_tokens")
    )

    cost = _attr(raw_usage, "cost")
    if cost is None and response is not None:
        hidden = getattr(response, "_hidden_params", None) or {}
        cost = hidden.get("response_cost") if isinstance(hidden, dict) else None
    if cost is not None:
        try:
            usage.cost_usd = float(cost)
        except (TypeError, ValueError):
            usage.cost_usd = None


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[dict] | None
    usage: LLMUsage = field(default_factory=LLMUsage)
    finish_reason: str | None = None


def _reasoning_piece(delta) -> str | None:
    """Extract a reasoning/thinking chunk from a streaming delta, if any.

    Probes the shapes different providers expose: LiteLLM's normalized
    ``reasoning_content`` (Anthropic, DeepSeek, OpenRouter), and the
    Anthropic-native ``thinking_blocks`` list as a fallback. Returns ``None``
    for non-reasoning models so they see zero behaviour change.
    """
    piece = getattr(delta, "reasoning_content", None)
    if piece:
        return piece
    blocks = getattr(delta, "thinking_blocks", None)
    if blocks:
        parts = [
            (b.get("thinking", "") if isinstance(b, dict) else getattr(b, "thinking", ""))
            for b in blocks
        ]
        joined = "".join(p for p in parts if p)
        if joined:
            return joined
    return None


def _partial_tail_len(buf: str, tag: str) -> int:
    """Length of the longest suffix of ``buf`` that is a proper prefix of ``tag``.

    Used to hold back a few trailing chars that might be the start of a tag
    split across streaming chunk boundaries (e.g. ``buf`` ends with ``"<thi"``).
    """
    for k in range(min(len(buf), len(tag) - 1), 0, -1):
        if buf.endswith(tag[:k]):
            return k
    return 0


class _ThinkSplitter:
    """Stream-split inline ``<think>...</think>`` reasoning out of content text.

    Some reasoning models — mostly local llama.cpp/vllm/Ollama endpoints
    serving DeepSeek-R1, QwQ, Qwen3, etc. — emit their chain-of-thought
    inline in the content stream wrapped in ``<think></think>`` rather than via
    a separate reasoning field. Feed each content piece in; the answer text is
    returned for the reply and the reasoning is returned separately for the
    thinking sink. Tags may straddle chunk boundaries, so a tail that could be
    a partial tag is held back until the next piece (or ``flush``).
    """

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False

    def feed(self, piece: str) -> tuple[str, str]:
        """Consume ``piece``; return ``(answer_text, thinking_text)``."""
        self._buf += piece
        answer: list[str] = []
        thinking: list[str] = []
        while self._buf:
            if not self._in_think:
                idx = self._buf.find(self.OPEN)
                if idx == -1:
                    keep = _partial_tail_len(self._buf, self.OPEN)
                    cut = len(self._buf) - keep
                    if cut > 0:
                        answer.append(self._buf[:cut])
                    self._buf = self._buf[cut:]
                    break
                if idx > 0:
                    answer.append(self._buf[:idx])
                self._buf = self._buf[idx + len(self.OPEN):]
                self._in_think = True
            else:
                idx = self._buf.find(self.CLOSE)
                if idx == -1:
                    keep = _partial_tail_len(self._buf, self.CLOSE)
                    cut = len(self._buf) - keep
                    if cut > 0:
                        thinking.append(self._buf[:cut])
                    self._buf = self._buf[cut:]
                    break
                if idx > 0:
                    thinking.append(self._buf[:idx])
                self._buf = self._buf[idx + len(self.CLOSE):]
                self._in_think = False
        return "".join(answer), "".join(thinking)

    def flush(self) -> tuple[str, str]:
        """Drain any held-back remainder at stream end as ``(answer, thinking)``."""
        rem, self._buf = self._buf, ""
        if not rem:
            return "", ""
        return ("", rem) if self._in_think else (rem, "")


async def _consume_stream(
    response_iter,
    on_text_delta: Callable[[str], Awaitable[None]],
    on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[str, dict[int, dict], LLMUsage, str | None]:
    """Drain a LiteLLM streaming response.

    Returns (full_text, tool_calls_by_index, usage, finish_reason). The last
    non-null finish_reason seen on any chunk wins. Reasoning chunks are routed
    to ``on_thinking_delta`` (when provided) and kept out of ``full_text``, so
    the returned answer never carries the model's thinking.
    """
    text_parts: list[str] = []
    tc_by_index: dict[int, dict] = {}
    usage = LLMUsage()
    finish_reason: str | None = None
    # Only strip inline <think> tags when a thinking sink is wired up — keeps
    # behaviour identical for callers that don't separate reasoning.
    think_splitter = _ThinkSplitter() if on_thinking_delta is not None else None

    async for chunk in response_iter:
        if getattr(chunk, "usage", None):
            usage.prompt_tokens = _attr(chunk.usage, "prompt_tokens", 0) or 0
            usage.completion_tokens = _attr(chunk.usage, "completion_tokens", 0) or 0
            usage.total_tokens = _attr(chunk.usage, "total_tokens", 0) or 0
            _populate_usage_dims(usage, chunk.usage, response=chunk)
            chunk_model = getattr(chunk, "model", None)
            if chunk_model:
                usage.model = chunk_model

        if not chunk.choices:
            continue
        fr = getattr(chunk.choices[0], "finish_reason", None)
        if fr:
            finish_reason = fr
        delta = chunk.choices[0].delta

        reasoning_piece = _reasoning_piece(delta)
        if reasoning_piece and on_thinking_delta is not None:
            await on_thinking_delta(reasoning_piece)

        text_piece = getattr(delta, "content", None)
        if text_piece:
            if think_splitter is not None:
                answer_piece, think_piece = think_splitter.feed(text_piece)
                if think_piece:
                    await on_thinking_delta(think_piece)
                if answer_piece:
                    text_parts.append(answer_piece)
                    await on_text_delta(answer_piece)
            else:
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

    if think_splitter is not None:
        answer_rem, think_rem = think_splitter.flush()
        if think_rem:
            await on_thinking_delta(think_rem)
        if answer_rem:
            text_parts.append(answer_rem)
            await on_text_delta(answer_rem)

    return "".join(text_parts), tc_by_index, usage, finish_reason


async def call_llm(
    model: str,
    messages: list[dict],
    tools: list[dict],
    api_base: str | None = None,
    openrouter_provider: str | None = None,
    on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
) -> LLMResponse:
    """Call LLM via LiteLLM, return normalized response.

    When ``on_text_delta`` is provided, switches to streaming mode and fires
    the callback for each text chunk. ``on_thinking_delta`` (optional) receives
    the model's reasoning/thinking chunks separately, so they never mix into
    the answer text. Tool calls and usage are still returned as a complete
    ``LLMResponse``.
    """
    kwargs = {
        "model": model,
        "messages": messages,
        "max_tokens": 16000,
        "num_retries": 0,  # disable LiteLLM's internal retries; we handle retries below
    }
    if api_base:
        kwargs["api_base"] = api_base
    extra_body: dict = {}
    if openrouter_provider:
        extra_body["provider"] = {"order": [openrouter_provider]}
    if model.startswith("openrouter/"):
        # Ask OpenRouter to include the authoritative cost in the response.
        extra_body["usage"] = {"include": True}
    if extra_body:
        kwargs["extra_body"] = extra_body
    if tools:
        kwargs["tools"] = tools

    streaming = on_text_delta is not None
    if streaming:
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

    # Debug: dump message shape (with base64 data URIs truncated) to help
    # diagnose multimodal handling. Only fires when LOG_LEVEL=DEBUG.
    if log.isEnabledFor(logging.DEBUG):
        def _summarize(msg):
            content = msg.get("content")
            if isinstance(content, list):
                parts = []
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "image_url":
                        url = (b.get("image_url") or {}).get("url", "")
                        parts.append(f"<image_url len={len(url)} prefix={url[:40]!r}>")
                    elif isinstance(b, dict) and b.get("type") == "text":
                        parts.append(f"<text {len(b.get('text', ''))} chars>")
                    else:
                        parts.append(str(type(b).__name__))
                return f"{{role: {msg.get('role')}, blocks: [{', '.join(parts)}]}}"
            return f"{{role: {msg.get('role')}, content: <{type(content).__name__} {len(content) if content else 0} chars>}}"
        log.debug("LLM call to %s with %d messages:", model, len(messages))
        for m in messages[-3:]:
            log.debug("  %s", _summarize(m))

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
        text, tc_by_index, usage, finish_reason = await _consume_stream(
            response, on_text_delta, on_thinking_delta)
        usage.elapsed_sec = time.monotonic() - t0
        if not usage.model:
            usage.model = model

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

        return LLMResponse(text=text or None, tool_calls=tool_calls, usage=usage, finish_reason=finish_reason)

    elapsed = time.monotonic() - t0
    choice = response.choices[0].message
    finish_reason = getattr(response.choices[0], "finish_reason", None)

    usage = LLMUsage(elapsed_sec=elapsed)
    usage.model = getattr(response, "model", None) or model
    if response.usage:
        usage.prompt_tokens = _attr(response.usage, "prompt_tokens", 0) or 0
        usage.completion_tokens = _attr(response.usage, "completion_tokens", 0) or 0
        usage.total_tokens = _attr(response.usage, "total_tokens", 0) or 0
        _populate_usage_dims(usage, response.usage, response=response)

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

    return LLMResponse(text=text, tool_calls=tool_calls, usage=usage, finish_reason=finish_reason)


async def describe_image(
    model: str,
    image_path: str,
    mime_type: str,
    user_prompt: str,
    api_base: str | None = None,
) -> str:
    """Single-shot multimodal call asking ``model`` to describe an image.

    Used as a pre-pass when the main agent model lacks vision support: the
    returned text is substituted in place of the image attachment so the
    main model can reason about it as text.

    Cached in-process by SHA-256 of the image bytes.
    """
    with open(image_path, "rb") as f:
        data = f.read()
    cache_key = hashlib.sha256(data).hexdigest()
    if cache_key in _description_cache:
        return _description_cache[cache_key]

    b64 = base64.b64encode(data).decode()
    prompt = (
        f"The user said: {user_prompt}\n\n" if user_prompt else ""
    ) + (
        "Describe this image in detail. Include any visible text verbatim, "
        "objects, layout, colors, and anything else that might be relevant "
        "for answering the user."
    )
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
            },
        ],
    }]

    kwargs = {
        "model": model,
        "messages": messages,
        "max_tokens": 1000,
        "num_retries": 0,
    }
    if api_base:
        kwargs["api_base"] = api_base

    for attempt in range(MAX_RETRIES):
        try:
            response = await litellm.acompletion(**kwargs)
            break
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status in (429, 502, 503) and attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                log.warning("Vision model returned %s, retrying in %ss (attempt %d/%d)",
                            status, delay, attempt + 1, MAX_RETRIES)
                await asyncio.sleep(delay)
            else:
                raise

    text = response.choices[0].message.content or ""
    _description_cache[cache_key] = text
    return text
