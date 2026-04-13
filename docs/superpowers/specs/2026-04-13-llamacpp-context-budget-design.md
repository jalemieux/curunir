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
    n_ctx: int | None = None                 # llama.cpp window, known only when api_base is set
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
    config.n_ctx = n_ctx
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

## Stats line redesign

The current stats line is confusing because `prompt: X tok` sums `prompt_tokens` across every iteration, double-counting the history prefix. Rewrite to report actual context usage plus this-turn work.

### Current (problematic)

```
prompt: 2226 tok | completion: 762 tok | 9.2 tok/s | 3 iter | 130.89s wall
```

- `prompt` sums every iteration's prompt → misleading (prefix counted N times).
- `iter` label is cryptic.
- No indication of real context occupancy.

### New format

llama.cpp (we know `n_ctx`):
```
ctx: 4821 tok (14% of 32768) | 762 completion tok | 9.2 tok/s | 3 steps | 130.9s
```

Hosted (no `n_ctx`):
```
ctx: 4821 tok | 762 completion tok | 9.2 tok/s | 3 steps | 130.9s
```

### Agent changes (`src/agent/agent.py`)

Track the **last** iteration's `prompt_tokens` separately, not just the sum:

```python
last_prompt_tokens = 0   # new accumulator

# inside the loop, after each response:
total_prompt_tokens += response.usage.prompt_tokens
last_prompt_tokens = response.usage.prompt_tokens   # overwrite
```

In `_finalize_stats`, replace `prompt_tokens: total_prompt_tokens` with:

```python
metadata["stats"] = {
    "context_tokens": last_prompt_tokens + total_completion_tokens,  # KV footprint after last call
    "completion_tokens": total_completion_tokens,
    "completion_tps": round(tps, 1),
    "llm_calls": llm_calls,
    "llm_elapsed_sec": round(total_llm_elapsed, 2),
    "wall_elapsed_sec": round(wall, 2),
    "iterations": 0,   # filled at return site
}
```

(`total_prompt_tokens` is no longer surfaced — it was misleading. If anyone needs prefix accounting they can log-dive.)

### n_ctx in stats (`run.py`)

When `api_base` is set, the resolved `n_ctx` is known at startup. Store it on the `AgentConfig` (add `n_ctx: int | None = None`) and include it in the `stats` dict surfaced to the CLI:

```python
if agent.config.n_ctx is not None:
    metadata["stats"]["n_ctx"] = agent.config.n_ctx
```

### CLI changes (`cli.py:143-160`)

Replace the existing stats rendering with:

```python
if verbose and stats and final:
    parts = []
    ctx_tok = stats.get("context_tokens")
    n_ctx = stats.get("n_ctx")
    if ctx_tok is not None:
        if n_ctx:
            pct = round(100 * ctx_tok / n_ctx)
            parts.append(f"ctx: {ctx_tok} tok ({pct}% of {n_ctx})")
        else:
            parts.append(f"ctx: {ctx_tok} tok")
    if stats.get("completion_tokens"):
        parts.append(f"{stats['completion_tokens']} completion tok")
    if stats.get("completion_tps"):
        parts.append(f"{stats['completion_tps']} tok/s")
    if stats.get("iterations"):
        parts.append(f"{stats['iterations']} steps")
    if stats.get("wall_elapsed_sec"):
        parts.append(f"{stats['wall_elapsed_sec']}s")
    if parts:
        stat_line = Text()
        stat_line.append("\n  ", style="dim")
        stat_line.append(" | ".join(parts), style="dim cyan")
        console.print(stat_line)
```

### Relationship to the context bar

The bar remains char-based (`used_chars / max_history_chars`) for the **steady-state between turns** — it's what the user sees at the prompt. The stats line is token-based and shows the **last LLM call's** context occupancy. The two numbers are consistent in direction but not identical (chars are estimated; tokens are exact, reported by the model). That's fine: the bar is a persistent glance, the stats line is a post-turn summary.

For llama.cpp users the stats-line percentage will often feel more trustworthy than the bar, which is another argument for using both — the bar tells you "am I trending toward a trim," the stats line tells you "what did the last call actually cost."

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

### Remove `MAX_HISTORY_CHARS` references

- `.env.example` — delete the line.
- `README.md:115` — delete the example `MAX_HISTORY_CHARS=16000`.
- `CLAUDE.md:122` — delete the bullet.

Replacement note in `CLAUDE.md` under "Key Environment Variables":

> History budget is auto-derived from the model's `n_ctx` when `api_base` is set (llama.cpp). For hosted models, history is not proactively trimmed; the agent falls back to halving the current history on `ContextWindowExceededError`.

### Split `docs/local-model-setup.md` into two documents

The current file mixes two concerns: *why* the orchestrator design works the way it does, and *how* to run it. Split so each document has one audience:

**New: `docs/orchestrator-architecture.md`** — the "why" doc. Reader is someone wanting to understand the design before or after using it.

Contents (carved from current file):
- Opening framing (current §1 paragraph).
- "How It Works" (current §"How It Works") — orchestrator architecture diagram, sub-agent model, summary compaction, what's disabled.
- "Context Budget" table, updated to reflect that the budget is now auto-derived from `n_ctx` rather than a fixed `MAX_HISTORY_CHARS` value. Add one sentence pointing the reader at `src/agent/context_budget.py` for the formula.
- "Customizing Sub-Agents" (current §) — conceptual, belongs with architecture.

**New: `docs/running-local-models.md`** — the "how" doc. Reader is someone who wants to get it running on their machine.

Contents:
- Hardware requirements + benchmarked performance (current §"Hardware Requirements").
- **Setup with llama.cpp alone** — the current §"Setup" with these edits:
  - Delete the `MAX_HISTORY_CHARS=16000` line from the `.env` block.
  - Delete the "Match your llama.cpp context window" comment.
  - Add one sentence: "Curunir reads `n_ctx` from llama.cpp's `/slots` at startup; no manual sizing required."
- **New section: Setup with llama-swap** — for users who want to swap between several local models without restarting Curunir.
  - One-paragraph intro: llama-swap is a proxy that fronts multiple llama.cpp instances and routes by model name in the OpenAI-compatible request (https://github.com/mostlygeek/llama-swap).
  - Minimal `config.yaml` example showing two models.
  - The Curunir `.env` change is just `API_BASE=http://localhost:<swap-port>/v1` and `MODEL=<one of the model names defined in llama-swap>`.
  - Note: Curunir reads `n_ctx` once at startup, so if llama-swap switches you to a model with a different `n_ctx` mid-session the budget will be wrong. For now, restart Curunir after changing the active model. (Mark as a known limitation; does not need solving in this spec.)
- "CLI Features" (current §) — the ctx bar description updated to match new behavior (bar char-based persistent, stats line token-based post-turn).
- "Troubleshooting" (current §) — drop the `MAX_HISTORY_CHARS` bullet. Add:
  - "llama.cpp unreachable at startup" — Curunir fails fast when `/slots` can't be reached; check that `llama-server` or llama-swap is running at `API_BASE`.
  - "Budget error at startup" — the computed budget was ≤ 0; the model's `n_ctx` is too small for the orchestrator's prompt + tool schemas. Increase `-c` on `llama-server` or pick a model with a larger window.

**Delete: `docs/local-model-setup.md`** after the split is done. Update links:
- `README.md:198` currently says `[docs/local-model-setup.md](docs/local-model-setup.md)` — repoint to both new docs: `[architecture](docs/orchestrator-architecture.md) and [setup guide](docs/running-local-models.md)`.
- Any other cross-links (`grep -rn 'local-model-setup'`) — update in the same pass.

## Files touched

- `src/config.py` — add `max_tokens` and `n_ctx`; change `max_history_chars` default to `None`.
- `src/llm.py` — accept `max_tokens` parameter.
- `src/agent/agent.py` — remove `Current time:` appends; conditional trim calls; reactive-trim fallback; thread `max_tokens` into `call_llm`; track `last_prompt_tokens`; restructure `_finalize_stats` to emit `context_tokens` instead of `prompt_tokens`.
- `run.py` — call `resolve_llamacpp_budget` after loading config and before constructing `Agent`; make `ctx_usage` computation conditional; inject `n_ctx` into the stats dict when known.
- `src/agent/context_budget.py` — **new**, holds `resolve_llamacpp_budget`, `_fetch_n_ctx`, `_compute_history_budget`.
- `cli.py` — rewrite stats line rendering (see Stats line redesign § CLI changes).
- `tests/test_context_budget.py` — **new**.
- `tests/test_agent.py` — update assertions (stats fields renamed; `Current time:` gone).
- `tests/conftest.py` — update fixtures if needed.
- `.env.example`, `README.md`, `CLAUDE.md` — remove `MAX_HISTORY_CHARS`, update cross-links.
- `docs/local-model-setup.md` — **delete** after split.
- `docs/orchestrator-architecture.md` — **new**, carved from current doc.
- `docs/running-local-models.md` — **new**, carved from current doc + new llama-swap section.

## Open questions

None — all resolved during brainstorm.
