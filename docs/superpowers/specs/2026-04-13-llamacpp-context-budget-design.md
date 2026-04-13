# llama.cpp Context Budget Design

**Date:** 2026-04-13
**Status:** Draft

## Problem

The agent uses a static `MAX_HISTORY_CHARS` env var (default 250k) to bound conversation history. With small self-hosted models (llama.cpp) whose context window is 4k–32k tokens, 250k chars is wildly too large: the CLI context-usage bar stays at 0% while real KV pressure inside sub-agents is near the limit, and proactive trim never kicks in before the model errors.

Two related issues surfaced alongside this:

1. `Agent.handle()` appends `\n\nCurrent time: {iso}` to the system prompt on every call, invalidating the llama.cpp KV-cache prefix every turn.
2. `call_llm` hardcodes `max_tokens=16000`, which exceeds the entire context window of common small local models.

## Goal

Derive `max_history_chars` from the model's real `n_ctx` when running against llama.cpp. For hosted models (Anthropic, OpenAI, OpenRouter), drop the fake number entirely — do not enforce a proactive trim and do not show a CLI bar, because we do not know the true window.

## Non-goals

- Per-sub-agent models. All sub-agents currently share `config.model`; measurement happens once.
- Token-accurate measurement. We use a conservative `chars_per_token = 3` approximation; the goal is a useful signal, not a precise count.
- Detection of non-llama.cpp local servers. Only llama.cpp `/props` is probed.

## Backend precedence

1. `api_base` is set → llama.cpp path: query `/props`, compute measured budget, enforce trim, show CLI bar.
2. `api_base` is unset → hosted path: `max_history_chars = None`, no proactive trim, no CLI bar. Rely on LiteLLM's `ContextWindowExceededError` for reactive trim.

The `MAX_HISTORY_CHARS` environment variable is **removed**. There is no manual override. If llama.cpp `/props` is unreachable at startup, the server fails to start with a clear error — we do not silently fall back to a guessed default.

## Config changes (`src/config.py`)

```python
@dataclass
class AgentConfig:
    model: str = "anthropic/claude-sonnet-4-20250514"
    api_base: str | None = None
    max_history_chars: int | None = None     # None = no proactive trim
    max_tokens: int = 16_000                 # per LLM call; may be clamped to n_ctx//2
    # ... existing fields
```

## Budget resolution

A new helper in `run.py` (or a small module `src/agent/context_budget.py`) performs the resolution **before** constructing the `Agent`:

```python
async def resolve_llamacpp_budget(config: AgentConfig, orchestrator_mode: bool) -> None:
    """Mutate config.max_history_chars and config.max_tokens in place.

    Called only when config.api_base is set. Raises on any failure — startup aborts.
    """
    n_ctx = await _fetch_n_ctx(config.api_base)   # GET /props

    if orchestrator_mode:
        static_prompt = build_orchestrator_prompt(config)
        tool_schemas = get_tool_schemas(["delegate"])
    else:
        static_prompt = build_static_prompt(config)
        tool_schemas = get_tool_schemas(None)

    budget = _compute_history_budget(
        n_ctx_tokens=n_ctx,
        static_prompt=static_prompt,
        tool_schemas=tool_schemas,
        max_tokens=config.max_tokens,
    )
    if budget <= 0:
        raise RuntimeError(
            f"Computed history budget is {budget} chars — model context "
            f"(n_ctx={n_ctx}) is too small for the current prompt and schemas."
        )

    config.max_history_chars = budget
    config.max_tokens = min(config.max_tokens, n_ctx // 2)
    logger.info(
        "llama.cpp context: n_ctx=%d, history_budget=%d chars, max_tokens=%d",
        n_ctx, budget, config.max_tokens,
    )
```

### Formula

```
CHARS_PER_TOKEN   = 3
SAFETY_MARGIN     = 500 chars

total_budget_chars  = n_ctx * CHARS_PER_TOKEN
prompt_chars        = len(static_prompt)
schema_chars        = len(json.dumps(tool_schemas))
response_chars      = max_tokens * CHARS_PER_TOKEN

budget = total_budget_chars - prompt_chars - schema_chars - response_chars - SAFETY_MARGIN
```

### `_fetch_n_ctx(api_base)`

Reuse the `/slots` endpoint already consumed by `_fetch_llamacpp_stats` in `run.py:65` — every slot exposes `n_ctx`, and all slots share the same window.

```python
async def _fetch_n_ctx(api_base: str) -> int:
    parsed = urlparse(api_base)
    url = urlunparse(parsed._replace(path="/slots"))
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        slots = resp.json()
    if not slots or "n_ctx" not in slots[0]:
        raise RuntimeError(f"llama.cpp /slots response missing n_ctx: {slots}")
    return int(slots[0]["n_ctx"])
```

Any HTTP error, timeout, or missing field aborts startup with a clear message.

## Agent behavior changes

### Remove `Current time:` append (`src/agent/agent.py:189, 194`)

Drop the `\n\nCurrent time: {iso}` string from both branches of the system-prompt construction. If the agent needs the current time, it can call `bash: date`; in orchestrator mode, it delegates to `system`.

### Conditional proactive trim (`src/agent/agent.py:195, 308`)

Replace every call to `_trim_history(history, max_chars=self.config.max_history_chars)` with:

```python
if self.config.max_history_chars is not None:
    _trim_history(history, max_chars=self.config.max_history_chars)
```

### Reactive trim fallback (`src/agent/agent.py:242`)

When `max_history_chars is None` (hosted path), the existing `half = self.config.max_history_chars // 2` calculation breaks. Change to:

```python
if self.config.max_history_chars is not None:
    half = self.config.max_history_chars // 2
else:
    half = _estimate_chars(history) // 2
_trim_history(history, max_chars=half)
```

### Use configured `max_tokens` (`src/llm.py:48`)

Replace the hardcoded `"max_tokens": 16000` with `config.max_tokens` threaded through `call_llm`. This means `call_llm`'s signature gains a `max_tokens: int` parameter (or accepts `config` directly — see Implementation plan decision).

## CLI behavior

`run.py:271-277` computes `ctx_usage` unconditionally today. Change:

```python
ctx_usage = None
if agent.config.max_history_chars is not None:
    session_history = agent.sessions.get(msg.session_id, [])
    if session_history:
        used = _estimate_chars(session_history)
        ctx_usage = min(used / agent.config.max_history_chars, 1.0)
```

`cli.py:_context_bar` already returns `""` when `usage is None`, so the bar disappears entirely for hosted models. No CLI code change needed.

## Tests

New: `tests/test_context_budget.py`
- Mock `/props` → returns `{"default_generation_settings": {"n_ctx": 8192}}`, verify `max_history_chars` computed correctly.
- Budget ≤ 0 raises a clear `RuntimeError`.
- `/props` unreachable raises at startup.
- `max_tokens` clamped to `n_ctx // 2` when originally larger.

Updates to existing tests:
- `tests/test_agent.py` — assert system prompt contains no `Current time:` string; verify proactive trim skipped when `max_history_chars is None`; verify reactive trim uses `_estimate_chars(history) // 2` in that case.
- `tests/conftest.py::agent_config` — set `max_history_chars=16_000` explicitly if existing tests rely on a bounded history.
- `tests/test_orchestrator_integration.py` — already sets `max_history_chars=16_000` (line 33), leave as-is.

## Documentation updates

Remove all references to `MAX_HISTORY_CHARS`:
- `.env.example` — delete the line.
- `README.md:115` — delete the example `MAX_HISTORY_CHARS=16000`.
- `CLAUDE.md:122` — delete the bullet.
- `docs/local-model-setup.md:62, 219` — delete the env example and the troubleshooting tip.

Add a short note in `docs/local-model-setup.md` under setup:

> Curunir queries llama.cpp's `/props` endpoint at startup to read the model's `n_ctx`, then derives a history budget that leaves room for the system prompt, tool schemas, and response. There is no `MAX_HISTORY_CHARS` setting — the budget is always computed.

Add to `CLAUDE.md` under "Key Environment Variables" (in place of the removed `MAX_HISTORY_CHARS` line):

> History budget is auto-derived from the model's `n_ctx` when `api_base` is set (llama.cpp). For hosted models, history is not proactively trimmed; the agent falls back to halving the current history on `ContextWindowExceededError`.

## Files touched

- `src/config.py` — add `max_tokens`, change `max_history_chars` default to `None`.
- `src/llm.py` — accept `max_tokens` parameter.
- `src/agent/agent.py` — remove `Current time:` appends; conditional trim calls; reactive-trim fallback; thread `max_tokens` into `call_llm`.
- `run.py` — call `resolve_llamacpp_budget` after loading config and before constructing `Agent`; make `ctx_usage` computation conditional.
- `src/agent/context_budget.py` — **new**, holds `resolve_llamacpp_budget`, `_fetch_n_ctx`, `_compute_history_budget`.
- `tests/test_context_budget.py` — **new**.
- `tests/test_agent.py` — update assertions.
- `tests/conftest.py` — update fixtures if needed.
- `.env.example`, `README.md`, `CLAUDE.md`, `docs/local-model-setup.md` — doc updates.

## Open questions

None — all resolved during brainstorm.
