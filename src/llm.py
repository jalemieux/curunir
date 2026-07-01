# src/llm.py
import asyncio
import base64
import hashlib
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import litellm

litellm.suppress_debug_info = True

log = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_BASE_DELAY = 2  # seconds

# HTTP statuses that are always worth a re-request (rate-limit + transient 5xx).
_RETRYABLE_STATUS = (429, 502, 503)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("Invalid %s=%r; falling back to %s", name, raw, default)
        return default


# Client-side per-request timeout (seconds), forwarded to litellm/httpx. In
# streaming mode httpx applies this as an inter-chunk *read* timeout, so it
# bounds how long curunir waits on a silent/hung upstream (e.g. an OpenRouter
# upstream idle timeout) before failing fast — letting the retry loop below
# re-request instead of blocking on the provider's much longer idle limit.
LLM_REQUEST_TIMEOUT = _env_float("LLM_REQUEST_TIMEOUT", 120.0)


def _retryable_exception_types() -> tuple[type, ...]:
    """Resolve retryable litellm exception classes, version-safely.

    Uses ``getattr`` so a litellm release that renames one of these doesn't
    break import. ``MidStreamFallbackError`` (raised when an upstream provider
    goes idle mid-stream) is a ``ServiceUnavailableError`` subclass, so it's
    covered by that entry without needing a direct reference to a symbol that
    isn't exported at the top level.
    """
    candidates = (
        getattr(litellm, "Timeout", None),
        getattr(litellm, "APIConnectionError", None),
        getattr(litellm, "ServiceUnavailableError", None),
        getattr(litellm, "InternalServerError", None),
    )
    return tuple(c for c in candidates if isinstance(c, type))


def _is_retryable(exc: Exception) -> bool:
    """Whether an LLM call/stream failure is worth a re-request.

    Retries on the explicit rate-limit/5xx statuses plus the timeout /
    connection / service-unavailable exception classes — the last of which
    includes LiteLLM's ``MidStreamFallbackError`` from an upstream idle timeout.
    """
    if getattr(exc, "status_code", None) in _RETRYABLE_STATUS:
        return True
    return isinstance(exc, _retryable_exception_types())


_QUOTA_MSG = "I've hit my allocated quota — please try again later."
_RATE_LIMIT_MSG = (
    "The LLM provider is rate-limiting me right now. Please try again in a minute."
)
_CREDITS_MSG = "My LLM provider is out of credits."
_QUOTA_SUBSTRINGS = ("key limit", "quota", "monthly limit", "insufficient_quota")
_CREDITS_SUBSTRINGS = ("insufficient credits", "add more using")


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
    if status == 402 or any(s in body for s in _CREDITS_SUBSTRINGS):
        return "credits_exhausted", _CREDITS_MSG
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


async def _consume_stream(
    response_iter,
    on_text_delta: Callable[[str], Awaitable[None]],
) -> tuple[str, dict[int, dict], LLMUsage, str | None]:
    """Drain a LiteLLM streaming response.

    Returns (full_text, tool_calls_by_index, usage, finish_reason). The last
    non-null finish_reason seen on any chunk wins.
    """
    text_parts: list[str] = []
    tc_by_index: dict[int, dict] = {}
    usage = LLMUsage()
    finish_reason: str | None = None

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

    return "".join(text_parts), tc_by_index, usage, finish_reason


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
        "timeout": LLM_REQUEST_TIMEOUT,
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

    # Track whether any *visible* text delta has streamed to the user. Once it
    # has, a mid-stream drop can't be retried — a re-request would duplicate
    # everything already shown. `_emit` flips the flag as it forwards deltas.
    emitted_any = False

    async def _emit(piece: str) -> None:
        nonlocal emitted_any
        emitted_any = True
        await on_text_delta(piece)

    text: str | None = None
    tc_by_index: dict[int, dict] = {}
    usage = LLMUsage()
    finish_reason: str | None = None

    for attempt in range(MAX_RETRIES):
        try:
            response = await litellm.acompletion(**kwargs)
            if streaming:
                # Drain the stream *inside* the retry loop: in streaming mode
                # acompletion returns the iterator immediately without error and
                # the real network read happens here, so a mid-stream drop (e.g.
                # an upstream idle timeout) is only catchable — and retryable —
                # from within the loop.
                text, tc_by_index, usage, finish_reason = await _consume_stream(
                    response, _emit
                )
            break
        except Exception as exc:
            # Never re-request once visible text has streamed — a retry would
            # duplicate output. The reported upstream idle timeout fires before
            # any content, so the pre-emission case is still covered.
            if emitted_any:
                raise
            if _is_retryable(exc) and attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                detail = getattr(exc, "status_code", None) or type(exc).__name__
                log.warning("LLM call failed (%s), retrying in %ss (attempt %d/%d)",
                            detail, delay, attempt + 1, MAX_RETRIES)
                await asyncio.sleep(delay)
            else:
                raise

    if streaming:
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
        "timeout": LLM_REQUEST_TIMEOUT,
    }
    if api_base:
        kwargs["api_base"] = api_base

    for attempt in range(MAX_RETRIES):
        try:
            response = await litellm.acompletion(**kwargs)
            break
        except Exception as exc:
            if _is_retryable(exc) and attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                detail = getattr(exc, "status_code", None) or type(exc).__name__
                log.warning("Vision model call failed (%s), retrying in %ss (attempt %d/%d)",
                            detail, delay, attempt + 1, MAX_RETRIES)
                await asyncio.sleep(delay)
            else:
                raise

    text = response.choices[0].message.content or ""
    _description_cache[cache_key] = text
    return text
