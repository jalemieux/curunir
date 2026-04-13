# llama.cpp Context Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `api_base` is set (llama.cpp), auto-derive `max_history_chars` and `max_tokens` from the model's real `n_ctx`. For hosted models, drop the fake number and the CLI bar. Fix the KV-cache-busting `Current time:` append and the misleading per-iteration `prompt_tokens` sum. Split `docs/local-model-setup.md` into an architecture doc and a running-guide doc with a llama-swap section.

**Architecture:** A new `src/agent/context_budget.py` module fetches `n_ctx` from llama.cpp `/slots` once at startup, measures the static system prompt + tool schemas, and mutates `AgentConfig` in place with the computed `max_history_chars`, clamped `max_tokens`, and `n_ctx`. Agent loop, CLI rendering, and the WebSocket stats payload thread `n_ctx` and `context_tokens` through so the user sees `ctx: 4821 tok (14% of 32768)` instead of the old prefix-summed `prompt: X tok`.

**Tech Stack:** Python 3.12+, asyncio, httpx, LiteLLM, Rich, pytest-asyncio.

**Reference spec:** `docs/superpowers/specs/2026-04-13-llamacpp-context-budget-design.md`.

---

## File Structure

**New:**
- `src/agent/context_budget.py` — budget resolution (`_fetch_n_ctx`, `_compute_history_budget`, `resolve_llamacpp_budget`).
- `tests/test_context_budget.py` — unit tests for the new module.
- `docs/orchestrator-architecture.md` — carved from current `docs/local-model-setup.md`.
- `docs/running-local-models.md` — carved from current `docs/local-model-setup.md` + new llama-swap section.

**Modified:**
- `src/config.py` — add `max_tokens`, `n_ctx`; change `max_history_chars` default to `None`.
- `src/llm.py` — accept `max_tokens` parameter.
- `src/agent/agent.py` — remove `Current time:` append; conditional proactive trim; reactive trim fallback; thread `max_tokens` into `call_llm`; track `last_prompt_tokens`; restructure `_finalize_stats`.
- `src/memory_extractor.py` — pass `config.max_tokens` to `call_llm`.
- `run.py` — call `resolve_llamacpp_budget` after config load and before `Agent` construction; remove `MAX_HISTORY_CHARS` env parse; conditional `ctx_usage` computation; inject `n_ctx` into stats.
- `cli.py` — rewrite stats-line rendering (lines 143-160).
- `tests/conftest.py` — set `max_history_chars=16_000` on the `agent_config` fixture.
- `tests/test_llm.py` — pass `max_tokens` arg in existing calls.
- `tests/test_agent.py` — drop any `Current time:` expectation; add tests for conditional trim and stats field names.
- `.env.example`, `README.md`, `CLAUDE.md` — remove `MAX_HISTORY_CHARS` references.

**Deleted:**
- `docs/local-model-setup.md` (after split).

---

## Task 1: Config field changes

**Files:**
- Modify: `src/config.py`
- Modify: `tests/conftest.py`

Changes the default of `max_history_chars` to `None`, adds `max_tokens` and `n_ctx`. Existing tests that assume a bounded history must set `max_history_chars` explicitly.

- [ ] **Step 1: Write the failing test** — assert the new defaults.

Add to `tests/test_config.py` (create the file if missing):

```python
from src.config import AgentConfig


def test_agent_config_defaults():
    cfg = AgentConfig()
    assert cfg.max_history_chars is None
    assert cfg.max_tokens == 16_000
    assert cfg.n_ctx is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_agent_config_defaults -v`
Expected: FAIL (either `max_history_chars` is `250_000`, or attribute missing).

- [ ] **Step 3: Update `src/config.py`**

Replace the `AgentConfig` class body with:

```python
@dataclass
class AgentConfig:
    model: str = "anthropic/claude-sonnet-4-20250514"
    api_base: str | None = None
    openrouter_provider: str | None = None
    max_iterations: int = 75
    max_history_chars: int | None = None
    max_tokens: int = 16_000
    n_ctx: int | None = None
    identity_file: Path = Path("./context/identity.md")
    context_dir: Path = Path("./context")
    skills_dir: Path = Path("./skills")
    agents_file: Path = Path("./context/agents.yaml")
```

- [ ] **Step 4: Update `tests/conftest.py`**

Change the `agent_config` fixture to pin `max_history_chars=16_000` so existing tests that rely on trim behavior keep working:

```python
@pytest.fixture
def agent_config(tmp_context, tmp_skills):
    """AgentConfig pointing at temporary directories."""
    return AgentConfig(
        identity_file=tmp_context / "identity.md",
        context_dir=tmp_context,
        skills_dir=tmp_skills,
        max_history_chars=16_000,
    )
```

- [ ] **Step 5: Run the new test + full suite**

Run: `pytest tests/test_config.py::test_agent_config_defaults -v`
Expected: PASS.

Run: `pytest tests/ -x`
Expected: All existing tests still pass (the fixture change keeps them bounded).

- [ ] **Step 6: Commit**

```bash
git add src/config.py tests/conftest.py tests/test_config.py
git commit -m "refactor: default max_history_chars to None; add max_tokens and n_ctx"
```

---

## Task 2: Thread `max_tokens` through `call_llm`

**Files:**
- Modify: `src/llm.py`
- Modify: `src/agent/agent.py` (two call sites)
- Modify: `src/memory_extractor.py` (one call site)
- Modify: `tests/test_llm.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_llm.py`:

```python
@pytest.mark.asyncio
async def test_call_llm_passes_max_tokens(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        class _Msg:
            content = "ok"
            tool_calls = None
        class _Choice:
            message = _Msg()
        class _Resp:
            choices = [_Choice()]
            usage = None
        return _Resp()

    monkeypatch.setattr("src.llm.litellm.acompletion", fake_acompletion)
    await call_llm("m", [{"role": "user", "content": "hi"}], [], max_tokens=4096)
    assert captured["max_tokens"] == 4096
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_llm.py::test_call_llm_passes_max_tokens -v`
Expected: FAIL — `call_llm` doesn't accept `max_tokens`.

- [ ] **Step 3: Update `src/llm.py`**

Change the signature and kwargs (lines 37-50):

```python
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
```

- [ ] **Step 4: Update call sites in `src/agent/agent.py`**

Both at line 238 and line 249, add `max_tokens=self.config.max_tokens` to the `call_llm(...)` invocation:

```python
response = await call_llm(
    self.config.model, messages, tool_schemas,
    max_tokens=self.config.max_tokens,
    api_base=self.config.api_base,
    openrouter_provider=self.config.openrouter_provider,
)
```

- [ ] **Step 5: Update `src/memory_extractor.py`**

At line 91, change:

```python
response = await call_llm(
    config.model, messages, tools=[],
    max_tokens=config.max_tokens,
    api_base=config.api_base,
    openrouter_provider=config.openrouter_provider,
)
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_llm.py -v`
Expected: PASS (including the new test and all existing tests).

Run: `pytest tests/ -x`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add src/llm.py src/agent/agent.py src/memory_extractor.py tests/test_llm.py
git commit -m "refactor: thread max_tokens from AgentConfig through call_llm"
```

---

## Task 3: `_compute_history_budget` pure function

**Files:**
- Create: `src/agent/context_budget.py`
- Create: `tests/test_context_budget.py`

A pure function so the formula is easy to test in isolation.

- [ ] **Step 1: Write failing tests**

Create `tests/test_context_budget.py`:

```python
import json
import pytest

from src.agent.context_budget import _compute_history_budget


def test_compute_history_budget_basic():
    # n_ctx=8192 tokens * 3 chars = 24576 chars
    # minus prompt=1000, schemas=2000, response=16000*3=48000 ... would be negative
    # so use a tiny prompt + small max_tokens to get a positive number
    budget = _compute_history_budget(
        n_ctx_tokens=8192,
        static_prompt="x" * 500,
        tool_schemas=[{"function": {"name": "t", "description": "", "parameters": {}}}],
        max_tokens=2000,
    )
    # 8192*3 - 500 - len(schemas_json) - 2000*3 - 500
    expected = 8192 * 3 - 500 - len(json.dumps(
        [{"function": {"name": "t", "description": "", "parameters": {}}}]
    )) - 2000 * 3 - 500
    assert budget == expected


def test_compute_history_budget_negative_when_window_too_small():
    budget = _compute_history_budget(
        n_ctx_tokens=1024,
        static_prompt="x" * 2000,
        tool_schemas=[],
        max_tokens=16_000,
    )
    assert budget <= 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_context_budget.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create `src/agent/context_budget.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_context_budget.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/context_budget.py tests/test_context_budget.py
git commit -m "feat: add _compute_history_budget pure function"
```

---

## Task 4: `_fetch_n_ctx` HTTP probe

**Files:**
- Modify: `src/agent/context_budget.py`
- Modify: `tests/test_context_budget.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_context_budget.py`:

```python
from unittest.mock import AsyncMock, patch

from src.agent.context_budget import _fetch_n_ctx


@pytest.mark.asyncio
async def test_fetch_n_ctx_returns_from_slots():
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: [{"id": 0, "n_ctx": 8192}]

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)

    with patch("src.agent.context_budget.httpx.AsyncClient", return_value=mock_client):
        n_ctx = await _fetch_n_ctx("http://localhost:8080/v1")
    assert n_ctx == 8192


@pytest.mark.asyncio
async def test_fetch_n_ctx_raises_on_empty_slots():
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: []

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)

    with patch("src.agent.context_budget.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError, match="missing n_ctx"):
            await _fetch_n_ctx("http://localhost:8080/v1")


@pytest.mark.asyncio
async def test_fetch_n_ctx_raises_on_missing_field():
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: [{"id": 0}]  # no n_ctx

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)

    with patch("src.agent.context_budget.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError, match="missing n_ctx"):
            await _fetch_n_ctx("http://localhost:8080/v1")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_context_budget.py -v`
Expected: FAIL — `_fetch_n_ctx` does not exist.

- [ ] **Step 3: Implement `_fetch_n_ctx` in `src/agent/context_budget.py`**

Append:

```python
async def _fetch_n_ctx(api_base: str) -> int:
    """GET {api_base root}/slots and return n_ctx from the first slot.

    llama.cpp's /slots endpoint returns a list of slot dicts, each with n_ctx.
    All slots share the same context window. Raises RuntimeError on HTTP error,
    empty slot list, or missing n_ctx field.
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_context_budget.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/context_budget.py tests/test_context_budget.py
git commit -m "feat: add _fetch_n_ctx to probe llama.cpp /slots"
```

---

## Task 5: `resolve_llamacpp_budget` orchestrator

**Files:**
- Modify: `src/agent/context_budget.py`
- Modify: `tests/test_context_budget.py`

Ties `_fetch_n_ctx` + `_compute_history_budget` together, mutates `AgentConfig` in place, and fails fast on bad inputs.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_context_budget.py`:

```python
from unittest.mock import patch

from src.agent.context_budget import resolve_llamacpp_budget
from src.config import AgentConfig


@pytest.mark.asyncio
async def test_resolve_budget_populates_config(tmp_context, tmp_skills):
    cfg = AgentConfig(
        api_base="http://localhost:8080/v1",
        max_tokens=2000,
        identity_file=tmp_context / "identity.md",
        context_dir=tmp_context,
        skills_dir=tmp_skills,
    )

    async def fake_fetch(api_base):
        return 8192

    with patch("src.agent.context_budget._fetch_n_ctx", fake_fetch):
        await resolve_llamacpp_budget(cfg, orchestrator_mode=False)

    assert cfg.n_ctx == 8192
    assert cfg.max_history_chars is not None and cfg.max_history_chars > 0
    assert cfg.max_tokens == 2000   # unchanged (< n_ctx//2)


@pytest.mark.asyncio
async def test_resolve_budget_clamps_max_tokens(tmp_context, tmp_skills):
    """When max_tokens > n_ctx//2, it gets clamped to n_ctx//2."""
    cfg = AgentConfig(
        api_base="http://localhost:8080/v1",
        max_tokens=20_000,   # will be clamped to 32768//2 = 16384
        identity_file=tmp_context / "identity.md",
        context_dir=tmp_context,
        skills_dir=tmp_skills,
    )

    async def fake_fetch(api_base):
        return 32768   # large enough that the clamped budget stays positive

    with patch("src.agent.context_budget._fetch_n_ctx", fake_fetch):
        await resolve_llamacpp_budget(cfg, orchestrator_mode=False)

    assert cfg.max_tokens == 16_384   # n_ctx // 2
    assert cfg.n_ctx == 32768
    assert cfg.max_history_chars > 0


@pytest.mark.asyncio
async def test_resolve_budget_raises_when_budget_nonpositive(tmp_context, tmp_skills):
    cfg = AgentConfig(
        api_base="http://localhost:8080/v1",
        max_tokens=16_000,
        identity_file=tmp_context / "identity.md",
        context_dir=tmp_context,
        skills_dir=tmp_skills,
    )

    async def fake_fetch(api_base):
        return 1024   # tiny window

    with patch("src.agent.context_budget._fetch_n_ctx", fake_fetch):
        with pytest.raises(RuntimeError, match="budget"):
            await resolve_llamacpp_budget(cfg, orchestrator_mode=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_context_budget.py::test_resolve_budget_populates_config -v`
Expected: FAIL — `resolve_llamacpp_budget` does not exist.

- [ ] **Step 3: Implement `resolve_llamacpp_budget`**

Append to `src/agent/context_budget.py`:

```python
async def resolve_llamacpp_budget(config, orchestrator_mode: bool) -> None:
    """Mutate config.max_history_chars, config.max_tokens, config.n_ctx in place.

    Must only be called when config.api_base is set. Aborts with RuntimeError on
    any failure — HTTP error, missing field, or computed budget <= 0.
    """
    # Imports are local to avoid a circular dependency with src.agent.agent.
    from src.agent.system_prompt import build_static_prompt, build_orchestrator_prompt
    from src.tools.schemas import get_tool_schemas

    n_ctx = await _fetch_n_ctx(config.api_base)

    if orchestrator_mode:
        static_prompt = build_orchestrator_prompt(config)
        tool_schemas = get_tool_schemas(["delegate"])
    else:
        static_prompt = build_static_prompt(config)
        tool_schemas = get_tool_schemas(None)

    # Clamp max_tokens before measuring so the reserve uses the value we'll
    # actually send.
    clamped_max_tokens = min(config.max_tokens, n_ctx // 2)

    budget = _compute_history_budget(
        n_ctx_tokens=n_ctx,
        static_prompt=static_prompt,
        tool_schemas=tool_schemas,
        max_tokens=clamped_max_tokens,
    )
    if budget <= 0:
        raise RuntimeError(
            f"Computed history budget is {budget} chars — model context "
            f"(n_ctx={n_ctx}) is too small for the current prompt and schemas."
        )

    config.n_ctx = n_ctx
    config.max_history_chars = budget
    config.max_tokens = clamped_max_tokens
    logger.info(
        "llama.cpp context: n_ctx=%d, history_budget=%d chars, max_tokens=%d",
        n_ctx, budget, config.max_tokens,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_context_budget.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/context_budget.py tests/test_context_budget.py
git commit -m "feat: add resolve_llamacpp_budget orchestrator"
```

---

## Task 6: Wire budget resolution into `run.py`

**Files:**
- Modify: `run.py`

- [ ] **Step 1: Remove `MAX_HISTORY_CHARS` env parsing**

At `run.py:319-325`, delete the `max_history_chars = os.environ.get("MAX_HISTORY_CHARS")` line and the corresponding spread entry. Final block:

```python
model = os.environ.get("MODEL")
api_base = os.environ.get("API_BASE")
openrouter_provider = os.environ.get("OPENROUTER_PROVIDER")
config = AgentConfig(
    **({"model": model} if model else {}),
    **({"api_base": api_base} if api_base else {}),
    **({"openrouter_provider": openrouter_provider} if openrouter_provider else {}),
)
```

- [ ] **Step 2: Call `resolve_llamacpp_budget` before constructing `Agent`**

Right after `orchestrator_mode = os.environ.get(...)` line (run.py:327), add:

```python
orchestrator_mode = os.environ.get("ORCHESTRATOR_MODE", "false").lower() == "true"

if config.api_base:
    from src.agent.context_budget import resolve_llamacpp_budget
    await resolve_llamacpp_budget(config, orchestrator_mode=orchestrator_mode)
```

- [ ] **Step 3: Manually verify**

Against a running `llama-server -c 8192`:

```bash
MODEL=openai/gemma-4-27b-it API_BASE=http://localhost:8080/v1 python run.py
```

Expected log: `llama.cpp context: n_ctx=8192, history_budget=<N> chars, max_tokens=4096`.

Against no api_base:

```bash
python run.py
```

Expected: starts without budget resolution log line (code path skipped).

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -x`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add run.py
git commit -m "feat: resolve llama.cpp context budget at startup"
```

---

## Task 7: Remove `Current time:` append

**Files:**
- Modify: `src/agent/agent.py`
- Modify: `tests/test_agent.py` (if present assertions exist)

This both reduces cache invalidation and removes a misleading injection.

- [ ] **Step 1: Write a failing test**

Add to `tests/test_agent.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

from src.agent.agent import Agent
from src.llm import LLMResponse, LLMUsage


@pytest.mark.asyncio
async def test_system_prompt_has_no_current_time(agent_config):
    captured = {}

    async def fake_call(model, messages, tools, **kwargs):
        captured["system"] = messages[0]["content"]
        return LLMResponse(text="ok", tool_calls=None, usage=LLMUsage())

    with patch("src.agent.agent.call_llm", new=fake_call):
        agent = Agent(agent_config)
        await agent.handle("hi", session_id="t1")

    assert "Current time:" not in captured["system"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_agent.py::test_system_prompt_has_no_current_time -v`
Expected: FAIL — the system prompt contains `Current time:`.

- [ ] **Step 3: Remove both appends in `src/agent/agent.py`**

At line 187-190, replace:

```python
            system_prompt = (
                self.static_prompt
                + f"\n\nCurrent time: {datetime.now().isoformat()}"
            )
```

with:

```python
            system_prompt = self.static_prompt
```

At line 194, replace:

```python
            system_prompt = self.static_prompt + f"\n\nCurrent time: {datetime.now().isoformat()}"
```

with:

```python
            system_prompt = self.static_prompt
```

Also remove the now-unused `from datetime import datetime` import at line 6 if nothing else uses it. (Grep inside `src/agent/agent.py` for `datetime`.)

- [ ] **Step 4: Run the new test + full suite**

Run: `pytest tests/test_agent.py::test_system_prompt_has_no_current_time -v`
Expected: PASS.

Run: `pytest tests/ -x`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/agent.py tests/test_agent.py
git commit -m "fix: drop per-call Current time append from system prompt"
```

---

## Task 8: Conditional proactive trim

**Files:**
- Modify: `src/agent/agent.py`
- Modify: `tests/test_agent.py`

- [ ] **Step 1: Write a failing test**

Add to `tests/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_no_proactive_trim_when_max_history_chars_is_none(agent_config, monkeypatch):
    """When max_history_chars is None, long histories are not trimmed proactively."""
    agent_config.max_history_chars = None

    call_count = {"n": 0}

    async def fake_call(model, messages, tools, **kwargs):
        call_count["n"] += 1
        return LLMResponse(text="done", tool_calls=None, usage=LLMUsage())

    with patch("src.agent.agent.call_llm", new=fake_call):
        agent = Agent(agent_config)
        # Preload a huge history.
        agent.sessions["t1"] = [
            {"role": "user", "content": "x" * 100_000},
            {"role": "assistant", "content": "y" * 100_000},
        ]
        await agent.handle("hi", session_id="t1")

    # 3 entries now (2 preloaded + the new user "hi" + assistant "done")
    assert len(agent.sessions["t1"]) == 4
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_agent.py::test_no_proactive_trim_when_max_history_chars_is_none -v`
Expected: FAIL — current `_trim_history` call crashes on `max_chars=None`.

- [ ] **Step 3: Wrap both trim call sites**

In `src/agent/agent.py`:

At line 195, replace:

```python
        _trim_history(history, max_chars=self.config.max_history_chars)
```

with:

```python
        if self.config.max_history_chars is not None:
            _trim_history(history, max_chars=self.config.max_history_chars)
```

At line 308, replace the same pattern with the same conditional.

- [ ] **Step 4: Run the new test + full suite**

Run: `pytest tests/test_agent.py::test_no_proactive_trim_when_max_history_chars_is_none -v`
Expected: PASS.

Run: `pytest tests/ -x`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/agent.py tests/test_agent.py
git commit -m "refactor: skip proactive trim when max_history_chars is None"
```

---

## Task 9: Reactive trim fallback for `None`

**Files:**
- Modify: `src/agent/agent.py`
- Modify: `tests/test_agent.py`

When `max_history_chars is None` and the provider raises `ContextWindowExceededError`, trim to half of the current history instead of crashing on `None // 2`.

- [ ] **Step 1: Write a failing test**

Add to `tests/test_agent.py`:

```python
import litellm


@pytest.mark.asyncio
async def test_reactive_trim_when_max_history_chars_is_none(agent_config):
    """On context overflow with max_history_chars=None, halve current history."""
    agent_config.max_history_chars = None
    call_count = {"n": 0}

    async def fake_call(model, messages, tools, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise litellm.ContextWindowExceededError(
                "too long", model="m", llm_provider="p"
            )
        return LLMResponse(text="recovered", tool_calls=None, usage=LLMUsage())

    with patch("src.agent.agent.call_llm", new=fake_call):
        agent = Agent(agent_config)
        agent.sessions["t1"] = [
            {"role": "user", "content": "x" * 50_000},
            {"role": "assistant", "content": "y" * 50_000},
        ]
        result = await agent.handle("follow-up", session_id="t1")

    assert result == "recovered"
    # History should have been trimmed (the new user msg still appended + assistant reply)
    assert sum(len(m.get("content", "")) for m in agent.sessions["t1"]) < 200_000
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_agent.py::test_reactive_trim_when_max_history_chars_is_none -v`
Expected: FAIL — `TypeError: unsupported operand type(s) for //: 'NoneType' and 'int'`.

- [ ] **Step 3: Update `src/agent/agent.py`**

At line 242, replace:

```python
                half = self.config.max_history_chars // 2
                logger.warning("[%s] context window exceeded, trimming history to %dk chars", sid, half // 1000)
                _trim_history(history, max_chars=half)
```

with:

```python
                if self.config.max_history_chars is not None:
                    half = self.config.max_history_chars // 2
                else:
                    half = _estimate_chars(history) // 2
                logger.warning("[%s] context window exceeded, trimming history to %dk chars", sid, half // 1000)
                _trim_history(history, max_chars=half)
```

- [ ] **Step 4: Run the new test + full suite**

Run: `pytest tests/test_agent.py::test_reactive_trim_when_max_history_chars_is_none -v`
Expected: PASS.

Run: `pytest tests/ -x`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/agent.py tests/test_agent.py
git commit -m "fix: reactive trim halves current history when max_history_chars is None"
```

---

## Task 10: Stats restructure — `context_tokens` replaces `prompt_tokens`

**Files:**
- Modify: `src/agent/agent.py`
- Modify: `tests/test_agent.py`

Track the **last** iteration's `prompt_tokens` and emit `context_tokens = last_prompt_tokens + total_completion_tokens`. Drop the misleading `total_prompt_tokens` field from the stats dict.

- [ ] **Step 1: Write a failing test**

Add to `tests/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_stats_emits_context_tokens(agent_config):
    """After a turn, stats.context_tokens reflects last prompt_tokens + total completion."""
    async def fake_call(model, messages, tools, **kwargs):
        # One LLM call, 1000 prompt tokens, 50 completion tokens.
        return LLMResponse(
            text="done",
            tool_calls=None,
            usage=LLMUsage(prompt_tokens=1000, completion_tokens=50, elapsed_sec=1.0),
        )

    with patch("src.agent.agent.call_llm", new=fake_call):
        agent = Agent(agent_config)
        metadata = {"stats": {}}
        await agent.handle("hi", session_id="t1", metadata=metadata)

    stats = metadata["stats"]
    assert stats["context_tokens"] == 1050  # 1000 (prompt) + 50 (completion)
    assert stats["completion_tokens"] == 50
    assert "prompt_tokens" not in stats
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_agent.py::test_stats_emits_context_tokens -v`
Expected: FAIL — stats dict has `prompt_tokens`, not `context_tokens`.

- [ ] **Step 3: Update `src/agent/agent.py`**

In `handle()`, right before the iteration loop starts (around line 204), add `last_prompt_tokens`:

```python
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_llm_elapsed = 0.0
        last_prompt_tokens = 0
        llm_calls = 0
        t_start = time.monotonic()
```

Inside the loop, after each `response = await call_llm(...)` succeeds (both at the main call site line 238 and the retry at line 249), update `last_prompt_tokens = response.usage.prompt_tokens`. The cleanest spot is right after `total_prompt_tokens += response.usage.prompt_tokens` at line 257:

```python
            total_prompt_tokens += response.usage.prompt_tokens
            total_completion_tokens += response.usage.completion_tokens
            total_llm_elapsed += response.usage.elapsed_sec
            last_prompt_tokens = response.usage.prompt_tokens
            llm_calls += 1
```

Replace `_finalize_stats` (lines 211-226) with:

```python
        def _finalize_stats() -> None:
            """Write accumulated LLM stats into metadata dict."""
            if metadata is None:
                return
            wall = time.monotonic() - t_start
            tps = total_completion_tokens / total_llm_elapsed if total_llm_elapsed > 0 else 0.0
            metadata["stats"] = {
                "context_tokens": last_prompt_tokens + total_completion_tokens,
                "completion_tokens": total_completion_tokens,
                "completion_tps": round(tps, 1),
                "llm_calls": llm_calls,
                "llm_elapsed_sec": round(total_llm_elapsed, 2),
                "wall_elapsed_sec": round(wall, 2),
                "iterations": 0,  # filled at return site
            }
```

- [ ] **Step 4: Run the new test + full suite**

Run: `pytest tests/test_agent.py::test_stats_emits_context_tokens -v`
Expected: PASS.

Run: `pytest tests/ -x`
Expected: PASS. (If any existing test asserted on the old `prompt_tokens` key, update it to use `context_tokens` with the same intent.)

- [ ] **Step 5: Commit**

```bash
git add src/agent/agent.py tests/test_agent.py
git commit -m "refactor: stats emit context_tokens, drop misleading prompt_tokens sum"
```

---

## Task 11: `run.py` — conditional `ctx_usage` + inject `n_ctx`

**Files:**
- Modify: `run.py`

- [ ] **Step 1: Make `ctx_usage` conditional**

Replace lines 271-277:

```python
        # Compute context usage ratio for CLI indicator
        ctx_usage = None
        session_history = agent.sessions.get(msg.session_id, [])
        if session_history:
            from src.agent.agent import _estimate_chars
            used = _estimate_chars(session_history)
            ctx_usage = min(used / agent.config.max_history_chars, 1.0)
```

with:

```python
        # Compute context usage ratio for CLI indicator (only when budget is known)
        ctx_usage = None
        if agent.config.max_history_chars is not None:
            session_history = agent.sessions.get(msg.session_id, [])
            if session_history:
                from src.agent.agent import _estimate_chars
                used = _estimate_chars(session_history)
                ctx_usage = min(used / agent.config.max_history_chars, 1.0)
```

- [ ] **Step 2: Inject `n_ctx` into stats**

Immediately after `metadata["stats"]["server"] = llama_stats` (around line 269), add:

```python
        # Surface n_ctx for CLI stats-line rendering.
        if agent.config.n_ctx is not None and metadata.get("stats"):
            metadata["stats"]["n_ctx"] = agent.config.n_ctx
```

- [ ] **Step 3: Manual verify**

Start the server with `API_BASE` set to a running `llama-server -c 8192`:

```bash
API_BASE=http://localhost:8080/v1 MODEL=openai/gemma python run.py
```

Connect with `python cli.py` and send one message. The CLI should still render the context bar and the stats line should now include `n_ctx`.

Start the server without `API_BASE` (hosted mode):

```bash
python run.py
```

The CLI should NOT render a context bar (usage stays `None`) and stats should NOT include `n_ctx`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/ -x`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add run.py
git commit -m "feat: conditional ctx_usage + surface n_ctx in stats"
```

---

## Task 12: CLI stats-line rewrite

**Files:**
- Modify: `cli.py`

No unit test — this is cosmetic terminal rendering; manually verify against a running server.

- [ ] **Step 1: Replace the stats block**

In `cli.py:143-160`, replace:

```python
                # Display stats in verbose mode
                stats = data.get("stats")
                if verbose and stats and final:
                    stat_line = Text()
                    stat_line.append("\n  ", style="dim")
                    parts = []
                    if stats.get("prompt_tokens"):
                        parts.append(f"prompt: {stats['prompt_tokens']} tok")
                    if stats.get("completion_tokens"):
                        parts.append(f"completion: {stats['completion_tokens']} tok")
                    if stats.get("completion_tps"):
                        parts.append(f"{stats['completion_tps']} tok/s")
                    if stats.get("iterations"):
                        parts.append(f"{stats['iterations']} iter")
                    if stats.get("wall_elapsed_sec"):
                        parts.append(f"{stats['wall_elapsed_sec']}s wall")
                    if parts:
                        stat_line.append(" | ".join(parts), style="dim cyan")
                        console.print(stat_line)
```

with:

```python
                # Display stats in verbose mode
                stats = data.get("stats")
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

- [ ] **Step 2: Manual verify**

Run Curunir against llama.cpp. Send one request. Expected terminal output:

```
ctx: 4821 tok (14% of 8192) | 762 completion tok | 9.2 tok/s | 3 steps | 130.9s
```

Run against a hosted model (no `API_BASE`). Expected:

```
ctx: 4821 tok | 762 completion tok | 9.2 tok/s | 3 steps | 130.9s
```

- [ ] **Step 3: Commit**

```bash
git add cli.py
git commit -m "refactor: rewrite CLI stats line — ctx%, clearer labels"
```

---

## Task 13: Remove `MAX_HISTORY_CHARS` from config files

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: `.env.example`**

Delete the `# MAX_HISTORY_CHARS=8000` line. Verify with:

```bash
grep -n MAX_HISTORY_CHARS .env.example
```

Expected: no output.

- [ ] **Step 2: `README.md`**

Remove line 115: `MAX_HISTORY_CHARS=16000`. If it sits in a code block for the local-model setup, leave the rest of that block intact. Verify:

```bash
grep -n MAX_HISTORY_CHARS README.md
```

Expected: no output.

- [ ] **Step 3: `CLAUDE.md`**

Remove the `MAX_HISTORY_CHARS` bullet at line 122. Replace with:

```markdown
- History budget is auto-derived from the model's `n_ctx` when `api_base` is set (llama.cpp). For hosted models, history is not proactively trimmed; the agent falls back to halving the current history on `ContextWindowExceededError`.
```

Verify:

```bash
grep -n MAX_HISTORY_CHARS CLAUDE.md
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add .env.example README.md CLAUDE.md
git commit -m "docs: remove MAX_HISTORY_CHARS env var references"
```

---

## Task 14: Create `docs/orchestrator-architecture.md`

**Files:**
- Create: `docs/orchestrator-architecture.md`

Carve the architecture/design content from `docs/local-model-setup.md`. Keep the original file intact for now — Task 16 deletes it after everything has been migrated.

- [ ] **Step 1: Create `docs/orchestrator-architecture.md`**

Write the following content:

```markdown
# Curunir Orchestrator Architecture

Curunir's orchestrator mode is designed for systems with limited VRAM and unified memory. Instead of a single powerful agent, a lightweight routing agent delegates tasks to specialized sub-agents, each running in a fresh, minimal context.

## Orchestrator Architecture

\```
User <-> CLI <-> Orchestrator Agent (delegate tool only)
                        |
          +-------------+-------------+
          v             v             v
    Files Agent   System Agent   Web Agent   ...
    (glob,grep,   (bash)         (web_fetch)
     read,edit,
     write)
\```

The **orchestrator** is a lightweight routing agent. It understands user intent and delegates to the right specialist. Its system prompt is ~300 tokens — just a name, a table of specialists, and delegation rules.

Each **sub-agent** is a fresh `Agent` instance spawned per delegation with:
- A minimal system prompt (~60 tokens)
- Only the tools it needs (2-5 tool schemas)
- A low iteration cap (5-10)
- No conversation history, no skills, no identity file

## Context Budget

At startup, Curunir queries llama.cpp's `/slots` endpoint once for the model's `n_ctx` and derives a history budget that leaves room for the system prompt, tool schemas, and the configured `max_tokens` response. See `src/agent/context_budget.py` for the formula.

An 8k-token window roughly splits as:

| Component | Tokens |
|-----------|--------|
| Orchestrator system prompt | ~300 |
| `delegate` tool schema | ~100 |
| Conversation history | ~remainder after reserve |

Sub-agents get the remainder of the window for their work (system prompt + tool schemas + task + tool results).

## Summary Compaction

After each delegation, the orchestrator compacts the raw tool call exchange into a one-line summary:

\```
Before: [assistant tool_call] + [tool result: 500 chars] = ~600 chars
After:  [summary] [system] uptime: 5 days = ~30 chars
\```

This keeps the orchestrator's history small so conversations can last longer within the tight context window.

## What's Disabled

In orchestrator mode, these features are turned off to save context and avoid complexity:

- **Skills system** — no `load_skill`, no skill manifest in the prompt
- **Automatic memory extraction** — memory is an explicit sub-agent instead
- **Concurrent tool execution** — one tool at a time (single inference process)
- **Large identity files** — replaced by a one-line name injection

## Customizing Sub-Agents

Sub-agents are defined in `context/agents.yaml`. The default configuration ships with six specialists:

| Agent | Tools | Use for |
|-------|-------|---------|
| `files` | glob, grep, read, edit, write | File operations |
| `system` | bash | Shell commands |
| `web` | web_fetch | Fetching URLs |
| `email` | email_read, email_send | Email |
| `scheduler` | schedule | Cron tasks |
| `memory` | read, write, glob | Persistent facts |

### Adding or modifying agents

Edit `context/agents.yaml`:

\```yaml
my-agent:
  description: "What this agent does (shown to orchestrator)"
  tools: [tool1, tool2]
  system_prompt: >
    You are a specialist. Do the task. Report concisely.
  max_iterations: 5
\```

The orchestrator's specialist table and the delegate tool's `agent` enum are auto-generated from this file at startup. No code changes needed.

### Writing effective sub-agent prompts

- Keep prompts under 100 tokens
- Say "do not explain your reasoning" to prevent chain-of-thought narration that burns context
- Say "report in under 100 words" to keep results compact
- Be specific about the agent's role so it stays focused
```

(Replace the triple-backtick placeholders `\``` ` above with real triple-backticks when writing the file.)

- [ ] **Step 2: Commit**

```bash
git add docs/orchestrator-architecture.md
git commit -m "docs: add orchestrator architecture doc"
```

---

## Task 15: Create `docs/running-local-models.md`

**Files:**
- Create: `docs/running-local-models.md`

Carve the setup/running content from `docs/local-model-setup.md` and add a new **Setup with llama-swap** section.

- [ ] **Step 1: Create `docs/running-local-models.md`**

Write the following content:

```markdown
# Running Curunir with Local Models

This guide walks through running Curunir against a locally hosted LLM (llama.cpp, optionally fronted by llama-swap). For the design rationale behind orchestrator mode, see [Orchestrator Architecture](orchestrator-architecture.md).

## Hardware Requirements

Reference hardware:

- **CPU:** AMD Ryzen 7000/8000 series (Phoenix APU)
- **GPU:** Integrated Radeon 780M iGPU (shared system memory)
- **RAM:** 32GB+ recommended (model + KV cache + OS)

Any system that can run a ~15GB quantized model via llama.cpp will work.

### Benchmarked Performance (Gemma 4 27B Q4_K_M)

| Backend | Prompt (tok/s) | Generation (tok/s) | Max context |
|---------|---------------|-------------------|-------------|
| Vulkan (iGPU) | ~314 | ~20 | ~4096 tokens |
| CPU (`-ngl 0`) | ~144 | ~14.3 | 8192+ tokens |

Expect 10-15 seconds per delegation round-trip. The orchestrator is designed around this latency.

## Setup with llama.cpp

### 1. Install and start llama.cpp

\```bash
# Build llama.cpp (see https://github.com/ggml-org/llama.cpp)
cmake -B build -DGGML_VULKAN=ON  # or -DGGML_CPU_ONLY=ON for CPU-only
cmake --build build --config Release

# Download a quantized model (example: Gemma 4 27B)
# Place it somewhere accessible, e.g. ~/models/

# Start the server
./build/bin/llama-server \
    -m ~/models/gemma-4-27b-it-Q4_K_M.gguf \
    --port 8080 \
    -ngl 99 \          # GPU layers (use 0 for CPU-only)
    -c 8192 \          # Context window
    --temp 0.6
\```

### 2. Configure Curunir

\```bash
cp .env.example .env
\```

Set these in `.env`:

\```bash
# Point at your local llama.cpp server
MODEL=openai/gemma-4-27b-it
API_BASE=http://localhost:8080/v1

# Enable orchestrator mode
ORCHESTRATOR_MODE=true
\```

Curunir reads `n_ctx` from llama.cpp's `/slots` at startup; no manual sizing required.

### 3. Customize your identity

Edit `context/identity.md`. Keep it short — every token counts. One or two lines is ideal:

\```markdown
# Hal

You are Hal, a personal assistant.
\```

### 4. Start Curunir

\```bash
python run.py
\```

In another terminal:

\```bash
python cli.py --host localhost
\```

You should see:

\```
Curunir (local mode)
Tip: I work best with focused requests. Ask me to do something specific.

> _
\```

## Setup with llama-swap

[llama-swap](https://github.com/mostlygeek/llama-swap) is a proxy that fronts multiple llama.cpp instances and routes requests to the right one based on the `model` field in the OpenAI-compatible request. Use it when you want to switch between local models without restarting Curunir.

### 1. Install llama-swap

See https://github.com/mostlygeek/llama-swap for installation. In short:

\```bash
go install github.com/mostlygeek/llama-swap@latest
\```

### 2. Write `config.yaml`

Minimal example with two models:

\```yaml
models:
  gemma-27b:
    cmd: >
      ~/llama.cpp/build/bin/llama-server
      -m ~/models/gemma-4-27b-it-Q4_K_M.gguf
      --port 8100 -ngl 99 -c 8192 --temp 0.6
    proxy: http://localhost:8100

  qwen-7b:
    cmd: >
      ~/llama.cpp/build/bin/llama-server
      -m ~/models/qwen-7b-instruct-Q5_K_M.gguf
      --port 8101 -ngl 99 -c 32768 --temp 0.6
    proxy: http://localhost:8101
\```

### 3. Start llama-swap

\```bash
llama-swap --config config.yaml --listen :8080
\```

### 4. Point Curunir at llama-swap

In `.env`:

\```bash
API_BASE=http://localhost:8080/v1
MODEL=openai/gemma-27b     # must match a name in config.yaml
ORCHESTRATOR_MODE=true
\```

### Known limitation

Curunir reads `n_ctx` **once at startup** from whichever llama.cpp instance llama-swap activates first. If you switch to a model with a different `n_ctx` mid-session (by changing `MODEL` and hitting llama-swap again), the budget will be stale. **Restart Curunir after changing the active model.** Automating live re-resolution is out of scope for now.

## CLI Features

### Context Usage Indicator

The prompt shows a 5-block bar indicating how much of the history budget is in use:

\```
[ctx: ██░░░] > check disk usage
[ctx: ████░] > now check memory
\```

At 4/5 blocks the bar turns yellow — time to `/clear` or wrap up. The bar appears only when `API_BASE` is set (llama.cpp). For hosted models the bar is hidden because the real window is unknown.

After each turn the stats line reports live KV usage as a percentage:

\```
ctx: 4821 tok (14% of 8192) | 762 completion tok | 9.2 tok/s | 3 steps | 130.9s
\```

### Delegation Progress

Tool calls show which sub-agent is working:

\```
  ├─ Delegate [system]: run df -h and report free space
\```

### Topic Reset

\```
> /clear
\```

Wipes orchestrator history. Persistent memory (files in `context/memory/`) survives.

## Troubleshooting

**Sub-agent timed out** — The model is too slow or the task is too complex. Try simplifying the request, or increase the timeout by setting `_TIMEOUT` in `src/tools/delegate.py`.

**llama.cpp unreachable at startup** — Curunir fails fast when `/slots` can't be reached. Check that `llama-server` (or `llama-swap`) is running and listening at the `API_BASE` URL.

**Budget error at startup** — The computed history budget was ≤ 0; the model's `n_ctx` is too small for the orchestrator's system prompt + tool schemas + reserved response. Increase `-c` on `llama-server` or pick a model with a larger window.

**Model generates garbage tool calls** — Small quantized models sometimes hallucinate invalid JSON or wrong agent names. The `agent` enum constraint helps, but if it persists, try a larger quantization (Q5_K_M, Q6_K) or a different model.

**Orchestrator delegates when it shouldn't** — The orchestrator prompt says "respond directly" for simple questions. If it over-delegates, you can edit the Rules section in `build_orchestrator_prompt()` in `src/agent/system_prompt.py`.
```

(Replace the `\``` ` placeholders with real triple-backticks when writing.)

- [ ] **Step 2: Commit**

```bash
git add docs/running-local-models.md
git commit -m "docs: add running-local-models guide with llama-swap section"
```

---

## Task 16: Delete `docs/local-model-setup.md` and fix cross-links

**Files:**
- Delete: `docs/local-model-setup.md`
- Modify: `README.md`
- Modify: any other file that links to `docs/local-model-setup.md`

- [ ] **Step 1: Find all cross-links**

Run:

```bash
grep -rn "local-model-setup" --include="*.md" --include="*.py" .
```

Note every hit.

- [ ] **Step 2: Update `README.md:198`**

Replace:

```markdown
Set `ORCHESTRATOR_MODE=true` to enable the small-model orchestrator. See [docs/local-model-setup.md](docs/local-model-setup.md).
```

with:

```markdown
Set `ORCHESTRATOR_MODE=true` to enable the small-model orchestrator. See the [architecture overview](docs/orchestrator-architecture.md) and [setup guide](docs/running-local-models.md).
```

- [ ] **Step 3: Update any other cross-links** found in Step 1 to point at the appropriate new doc (architecture or setup). Prefer the setup guide for install/configure instructions, the architecture doc for design context.

- [ ] **Step 4: Delete the old file**

```bash
git rm docs/local-model-setup.md
```

- [ ] **Step 5: Verify no stale links**

```bash
grep -rn "local-model-setup" --include="*.md" --include="*.py" .
```

Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "docs: split local-model-setup into architecture + running guides"
```

---

## Final verification

- [ ] **Run the full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Manual end-to-end with llama.cpp**

Start `llama-server -c 8192 -m <model>` on port 8080. Then:

```bash
API_BASE=http://localhost:8080/v1 MODEL=openai/<name> ORCHESTRATOR_MODE=true python run.py
```

In another terminal:

```bash
python cli.py
```

Send one message. Verify:
- Startup log line: `llama.cpp context: n_ctx=8192, history_budget=<N> chars, max_tokens=4096`.
- Context bar appears on the input line.
- Stats line after the response reads: `ctx: <N> tok (<P>% of 8192) | <M> completion tok | ...`.
- No `Current time:` substring in any request body (verify with `LOG_LEVEL=DEBUG`).

- [ ] **Manual end-to-end with a hosted model**

Without `API_BASE`:

```bash
MODEL=anthropic/claude-sonnet-4-20250514 ANTHROPIC_API_KEY=<key> python run.py
```

Send one message. Verify:
- No startup budget log line.
- Context bar does **not** appear (usage is `None`).
- Stats line shows `ctx: <N> tok | ...` **without** the `(P% of N)` suffix.

- [ ] **Doc sanity**

```bash
ls docs/local-model-setup.md          # should error: no such file
ls docs/orchestrator-architecture.md  # exists
ls docs/running-local-models.md       # exists
grep -rn MAX_HISTORY_CHARS .          # no hits
grep -rn local-model-setup --include="*.md" --include="*.py" .  # no hits
```

If any of these fail, revisit the relevant task.
