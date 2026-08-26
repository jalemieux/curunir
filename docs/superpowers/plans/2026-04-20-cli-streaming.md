# CLI Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream assistant text token-by-token from the LLM through the WebSocket channel to the CLI so users see incremental progress instead of waiting for the agent's full turn.

**Architecture:** Callback-based plumbing. `call_llm` gains an optional `on_text_delta` callback; when set, switches to `litellm.acompletion(stream=True)` and fires the callback per chunk while still returning the same `LLMResponse` shape. `Agent.handle()` and `agent_worker` thread the callback through. `OutgoingMessage` gets a new `delta: bool` field; the WS payload gets a matching key. The CLI accumulates deltas into a `rich.live.Live(transient=True)` region rendered as plain `Text`, and on flush replaces it with a `Markdown` render. Email channel is untouched.

**Tech Stack:** Python 3.12, asyncio, LiteLLM (already in use), `rich` (already in use for CLI), `websockets` (already in use).

**Spec:** `docs/superpowers/specs/2026-04-20-cli-streaming-design.md`

---

## File Map

- **Modify** `src/channels/base.py` — add `delta: bool = False` to `OutgoingMessage`.
- **Modify** `src/channels/ws.py` — include `delta` in the JSON payload.
- **Modify** `src/llm.py` — add `on_text_delta` parameter to `call_llm`; implement streaming path.
- **Modify** `src/agent/agent.py` — add `on_text_delta` parameter to `Agent.handle()`; thread to both `call_llm` call sites.
- **Modify** `run.py` — add `on_text_delta` callback in `agent_worker` that emits delta `OutgoingMessage`s.
- **Modify** `cli.py` — add stream-region state machine to `output_loop`.
- **Modify** `tests/test_channels.py` — assert `delta` defaults to False on `OutgoingMessage`.
- **Modify** `tests/test_ws_channel.py` — assert `delta` field in JSON payload.
- **Modify** `tests/test_llm.py` — add streaming tests with mocked async-iterator response.
- **Modify** `tests/test_agent.py` — assert `on_text_delta` is forwarded to `call_llm`.

---

## Task 1: Add `delta` field to `OutgoingMessage`

**Files:**
- Modify: `src/channels/base.py`
- Test: `tests/test_channels.py`

- [ ] **Step 1: Look at the existing test file to find a good place to add a test**

Run: `grep -n "OutgoingMessage" <repo-root>/tests/test_channels.py | head -10`

If `tests/test_channels.py` doesn't already have an `OutgoingMessage` defaults test, append the test below. If it does, add the new assertion alongside it.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_channels.py`:

```python
from src.channels.base import OutgoingMessage


def test_outgoing_message_delta_defaults_false():
    msg = OutgoingMessage(
        content="hi",
        channel="cli",
        session_id="s1",
        reply_address={},
    )
    assert msg.delta is False


def test_outgoing_message_delta_can_be_set():
    msg = OutgoingMessage(
        content="chunk",
        channel="cli",
        session_id="s1",
        reply_address={},
        delta=True,
    )
    assert msg.delta is True
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_channels.py::test_outgoing_message_delta_defaults_false tests/test_channels.py::test_outgoing_message_delta_can_be_set -v`

Expected: FAIL — `OutgoingMessage` has no `delta` field.

- [ ] **Step 4: Add the field**

Edit `src/channels/base.py`. The current dataclass:

```python
@dataclass
class OutgoingMessage:
    content: str
    channel: str
    session_id: str
    reply_address: dict
    tool_calls: list[str] | None = None
    final: bool = True
    attachments: list[dict] | None = None
    workflow: dict | None = None
    stats: dict | None = None
```

Add `delta: bool = False` after `final`:

```python
@dataclass
class OutgoingMessage:
    content: str
    channel: str
    session_id: str
    reply_address: dict
    tool_calls: list[str] | None = None
    final: bool = True
    delta: bool = False
    attachments: list[dict] | None = None
    workflow: dict | None = None
    stats: dict | None = None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_channels.py -v`
Expected: PASS for both new tests; no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/channels/base.py tests/test_channels.py
git commit -m "feat: add delta field to OutgoingMessage for streaming chunks"
```

---

## Task 2: Include `delta` in WS payload

**Files:**
- Modify: `src/channels/ws.py:84-101`
- Test: `tests/test_ws_channel.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ws_channel.py`:

```python
@pytest.mark.asyncio
async def test_send_includes_delta_field():
    """delta field from OutgoingMessage is included in the JSON payload."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 9)
    task = await _start_channel(ch)

    try:
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 9}") as ws:
            await asyncio.sleep(0.05)
            outgoing = OutgoingMessage(
                content="chunk",
                channel="cli",
                session_id=SESSION_ID,
                reply_address={},
                delta=True,
                final=False,
            )
            await ch.send(outgoing)

            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            data = json.loads(raw)
            assert data["delta"] is True
            assert data["content"] == "chunk"
            assert data["final"] is False
    finally:
        await _stop_channel(task)


@pytest.mark.asyncio
async def test_send_delta_defaults_false_in_payload():
    """delta key in JSON payload defaults to False when not set on OutgoingMessage."""
    q = asyncio.Queue()
    ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 10)
    task = await _start_channel(ch)

    try:
        async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 10}") as ws:
            await asyncio.sleep(0.05)
            outgoing = OutgoingMessage(
                content="full",
                channel="cli",
                session_id=SESSION_ID,
                reply_address={},
            )
            await ch.send(outgoing)

            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            data = json.loads(raw)
            assert data["delta"] is False
    finally:
        await _stop_channel(task)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ws_channel.py::test_send_includes_delta_field tests/test_ws_channel.py::test_send_delta_defaults_false_in_payload -v`
Expected: FAIL — `data["delta"]` is `KeyError`.

- [ ] **Step 3: Modify the payload in ws.py**

Edit `src/channels/ws.py`. The current `send` method's payload construction:

```python
payload: dict = {
    "content": msg.content,
    "tool_calls": msg.tool_calls,
    "final": msg.final,
    "attachments": msg.attachments if msg.attachments else None,
    "workflow": msg.workflow,
    "stats": msg.stats,
}
```

Add `"delta": msg.delta`:

```python
payload: dict = {
    "content": msg.content,
    "tool_calls": msg.tool_calls,
    "final": msg.final,
    "delta": msg.delta,
    "attachments": msg.attachments if msg.attachments else None,
    "workflow": msg.workflow,
    "stats": msg.stats,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ws_channel.py -v`
Expected: PASS for both new tests; no regressions.

- [ ] **Step 5: Commit**

```bash
git add src/channels/ws.py tests/test_ws_channel.py
git commit -m "feat: forward delta field in WebSocket JSON payload"
```

---

## Task 3: Stream LLM response when `on_text_delta` is provided

**Files:**
- Modify: `src/llm.py`
- Test: `tests/test_llm.py`

This task adds the streaming code path inside `call_llm`. When `on_text_delta` is `None`, behavior is unchanged. When provided, `litellm.acompletion(stream=True, stream_options={"include_usage": True})` is called and chunks are aggregated into the existing `LLMResponse` shape.

LiteLLM streams chunks where each chunk has:
- `chunk.choices[0].delta.content` — incremental text (string or None).
- `chunk.choices[0].delta.tool_calls` — list of partial tool-call deltas, each with `index`, `id` (first chunk only), `function.name` (first chunk only), `function.arguments` (concatenated).
- `chunk.usage` — only present on the terminal chunk when `stream_options={"include_usage": True}`.

- [ ] **Step 1: Write a failing test for delta callback firing per chunk**

Append to `tests/test_llm.py`:

```python
def _make_stream_chunk(content: str | None = None, tool_call_deltas: list | None = None,
                       usage: tuple | None = None):
    """Build a mock LiteLLM streaming chunk.

    tool_call_deltas: list of dicts like {"index": 0, "id": "call_x",
        "function": {"name": "bash", "arguments": "{\"cmd\":"}}.
    usage: tuple (prompt_tokens, completion_tokens, total_tokens) or None.
    """
    delta = MagicMock()
    delta.content = content
    if tool_call_deltas is None:
        delta.tool_calls = None
    else:
        tc_mocks = []
        for d in tool_call_deltas:
            tc = MagicMock()
            tc.index = d["index"]
            tc.id = d.get("id")
            fn = d.get("function", {})
            tc.function = MagicMock()
            tc.function.name = fn.get("name")
            tc.function.arguments = fn.get("arguments")
            tc_mocks.append(tc)
        delta.tool_calls = tc_mocks
    choice = MagicMock()
    choice.delta = delta
    chunk = MagicMock()
    chunk.choices = [choice]
    if usage is None:
        chunk.usage = None
    else:
        u = MagicMock()
        u.prompt_tokens, u.completion_tokens, u.total_tokens = usage
        chunk.usage = u
    return chunk


def _async_iter(chunks):
    """Wrap a list as an async iterator that supports `async for`."""
    class _AIter:
        def __init__(self, items):
            self._it = iter(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    return _AIter(chunks)


@pytest.mark.asyncio
async def test_stream_text_fires_callback_per_chunk():
    chunks = [
        _make_stream_chunk(content="Hel"),
        _make_stream_chunk(content="lo "),
        _make_stream_chunk(content="world"),
        _make_stream_chunk(usage=(10, 3, 13)),
    ]

    received: list[str] = []

    async def on_delta(text: str):
        received.append(text)

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=_async_iter(chunks))
        result = await call_llm(
            "test-model", [{"role": "user", "content": "hi"}], [],
            on_text_delta=on_delta,
        )

    assert received == ["Hel", "lo ", "world"]
    assert result.text == "Hello world"
    assert result.tool_calls is None
    assert result.usage.prompt_tokens == 10
    assert result.usage.completion_tokens == 3
    assert result.usage.total_tokens == 13
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm.py::test_stream_text_fires_callback_per_chunk -v`
Expected: FAIL — `call_llm` doesn't accept `on_text_delta`.

- [ ] **Step 3: Implement the streaming branch in `call_llm`**

Edit `src/llm.py`. Replace the entire file contents:

```python
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
        # Usage chunks may arrive without choices.
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

    When `on_text_delta` is provided, uses streaming mode and fires the
    callback for each text chunk. Tool calls and usage are still returned
    as a complete `LLMResponse`.
    """
    kwargs = {
        "model": model,
        "messages": messages,
        "max_tokens": 16000,
        "num_retries": 0,
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
```

- [ ] **Step 4: Run the streaming test**

Run: `pytest tests/test_llm.py::test_stream_text_fires_callback_per_chunk -v`
Expected: PASS.

- [ ] **Step 5: Run all `test_llm.py` tests to confirm no regressions**

Run: `pytest tests/test_llm.py -v`
Expected: All tests PASS, including the existing non-streaming ones.

- [ ] **Step 6: Add a failing test for tool-call accumulation across streaming chunks**

Append to `tests/test_llm.py`:

```python
@pytest.mark.asyncio
async def test_stream_accumulates_tool_call_arguments():
    chunks = [
        _make_stream_chunk(content="Let me check"),
        _make_stream_chunk(tool_call_deltas=[
            {"index": 0, "id": "call_abc",
             "function": {"name": "bash", "arguments": "{\"comm"}},
        ]),
        _make_stream_chunk(tool_call_deltas=[
            {"index": 0, "function": {"arguments": "and\": \"echo hi\"}"}},
        ]),
        _make_stream_chunk(usage=(20, 5, 25)),
    ]

    async def on_delta(text: str):
        pass

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=_async_iter(chunks))
        result = await call_llm("test-model", [], [], on_text_delta=on_delta)

    assert result.text == "Let me check"
    assert result.tool_calls is not None
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc["id"] == "call_abc"
    assert tc["function"]["name"] == "bash"
    assert tc["function"]["arguments"] == '{"command": "echo hi"}'
```

- [ ] **Step 7: Run the new test**

Run: `pytest tests/test_llm.py::test_stream_accumulates_tool_call_arguments -v`
Expected: PASS — the implementation already accumulates by `index`.

- [ ] **Step 8: Add a test confirming non-streaming path is untouched**

This is already covered by the existing `test_text_response`, `test_tool_call_response`, and `test_both_text_and_tool_calls`. Re-running the full file is sufficient verification.

Run: `pytest tests/test_llm.py -v`
Expected: All PASS.

- [ ] **Step 9: Commit**

```bash
git add src/llm.py tests/test_llm.py
git commit -m "feat: add streaming mode to call_llm via on_text_delta callback"
```

---

## Task 4: Thread `on_text_delta` through `Agent.handle()`

**Files:**
- Modify: `src/agent/agent.py:143-235`
- Test: `tests/test_agent.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent.py`, inside `class TestAgentHandle`:

```python
    async def test_forwards_on_text_delta_to_call_llm(self, agent):
        mock_response = LLMResponse(text="streamed", tool_calls=None)

        async def cb(text: str):
            pass

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_call:
            await agent.handle("hi", "test-session", on_text_delta=cb)

        assert mock_call.call_count == 1
        # call_llm is called with positional and keyword args; on_text_delta
        # must appear among the kwargs.
        kwargs = mock_call.call_args.kwargs
        assert kwargs.get("on_text_delta") is cb

    async def test_forwards_on_text_delta_across_iterations(self, agent):
        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "echo hi"})},
            }],
        )
        text_response = LLMResponse(text="Done!", tool_calls=None)

        async def cb(text: str):
            pass

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[tool_response, text_response]) as mock_call:
            await agent.handle("run", "s1", on_text_delta=cb)

        assert mock_call.call_count == 2
        for call in mock_call.call_args_list:
            assert call.kwargs.get("on_text_delta") is cb
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agent.py::TestAgentHandle::test_forwards_on_text_delta_to_call_llm tests/test_agent.py::TestAgentHandle::test_forwards_on_text_delta_across_iterations -v`
Expected: FAIL — `Agent.handle` doesn't accept `on_text_delta`.

- [ ] **Step 3: Add the parameter and thread it through**

Edit `src/agent/agent.py`. Update `Agent.handle`'s signature to add `on_text_delta`:

```python
async def handle(
    self, message: str | list, session_id: str,
    on_tool_call=None, attachments: list[dict] | None = None,
    system_task_prompt: str | None = None,
    metadata: dict | None = None,
    stop_event: asyncio.Event | None = None,
    on_text_delta=None,
) -> str:
```

Update both `call_llm` invocations inside the loop (the primary call and the post-context-overflow retry call) to pass `on_text_delta=on_text_delta`.

The primary call (currently lines 215-216):

```python
response = await call_llm(self.config.model, messages, tool_schemas, api_base=self.config.api_base, openrouter_provider=self.config.openrouter_provider)
```

becomes:

```python
response = await call_llm(
    self.config.model, messages, tool_schemas,
    api_base=self.config.api_base,
    openrouter_provider=self.config.openrouter_provider,
    on_text_delta=on_text_delta,
)
```

And the retry call after context-overflow trim (currently around lines 226-227):

```python
response = await call_llm(self.config.model, messages, tool_schemas, api_base=self.config.api_base, openrouter_provider=self.config.openrouter_provider)
```

becomes:

```python
response = await call_llm(
    self.config.model, messages, tool_schemas,
    api_base=self.config.api_base,
    openrouter_provider=self.config.openrouter_provider,
    on_text_delta=on_text_delta,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agent.py -v`
Expected: All tests PASS, including the two new ones and all existing.

- [ ] **Step 5: Commit**

```bash
git add src/agent/agent.py tests/test_agent.py
git commit -m "feat: thread on_text_delta callback through Agent.handle"
```

---

## Task 5: Wire delta callback in `agent_worker`

**Files:**
- Modify: `run.py:164-278`

This change is in the runtime wiring (not in a unit-tested module). It's small enough to verify by inspection plus the manual end-to-end test in Task 7.

- [ ] **Step 1: Read the existing `on_tool_call` definition for pattern**

Open `run.py` and find the `on_tool_call` async def inside `agent_worker` (around line 197). The new callback follows the same pattern.

- [ ] **Step 2: Add the delta callback and pass it to `agent.handle`**

Edit `run.py`. Inside `agent_worker`, after the `on_tool_call` definition, add:

```python
        async def on_text_delta(chunk: str):
            await out_queue.put(OutgoingMessage(
                content=chunk,
                channel=msg.channel,
                session_id=msg.session_id,
                reply_address=msg.reply_address,
                delta=True,
                final=False,
            ))
```

Then update the `agent.handle(...)` call to pass `on_text_delta=on_text_delta`. The current call:

```python
handle_task = asyncio.create_task(
    agent.handle(
        content, msg.session_id,
        on_tool_call=on_tool_call, attachments=attachments,
        metadata=metadata, stop_event=stop_event,
    )
)
```

becomes:

```python
handle_task = asyncio.create_task(
    agent.handle(
        content, msg.session_id,
        on_tool_call=on_tool_call, attachments=attachments,
        metadata=metadata, stop_event=stop_event,
        on_text_delta=on_text_delta,
    )
)
```

- [ ] **Step 3: Sanity-check the existing test suite**

Run: `pytest tests/ -x -q`
Expected: All tests PASS. (No tests touch `run.py` directly; this is a sanity check that nothing imported from it broke.)

- [ ] **Step 4: Commit**

```bash
git add run.py
git commit -m "feat: emit streaming delta messages from agent_worker"
```

---

## Task 6: CLI delta rendering

**Files:**
- Modify: `cli.py`

This task is the largest single change. The CLI's `output_loop` gains a stream-region state machine using `rich.live.Live(transient=True)`. There are no automated tests for `cli.py` today; verification is by manual run in Task 7.

- [ ] **Step 1: Add the Live import**

Edit `cli.py`. The current import block at the top:

```python
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text
```

becomes:

```python
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text
```

- [ ] **Step 2: Add stream state to `output_loop`**

Edit `cli.py`'s `output_loop` function. The current function starts with:

```python
async def output_loop(ws: websockets.ClientConnection) -> None:
    pending_tool_calls: list[str] = []

    def flush_tool_calls() -> None:
        ...
```

Add stream state and a flush helper at the top of `output_loop`, before the existing `pending_tool_calls`:

```python
async def output_loop(ws: websockets.ClientConnection) -> None:
    # Streaming state: when the server sends delta messages, we accumulate
    # them in `stream_buffer` and display them in a transient Live region.
    # On the next non-delta message, we close the Live (which erases the
    # plain-text region) and re-print the buffer as Markdown.
    stream_buffer: list[str] = []
    stream_live: Live | None = None

    def flush_stream() -> str:
        """Stop the Live region and return the accumulated text.

        The caller decides whether to render the text (we don't render here
        because the final-message handler may want to suppress its own
        Markdown print to avoid double-rendering).
        """
        nonlocal stream_live
        if stream_live is None:
            return ""
        stream_live.stop()
        stream_live = None
        text = "".join(stream_buffer)
        stream_buffer.clear()
        return text

    pending_tool_calls: list[str] = []
    ...
```

- [ ] **Step 3: Handle delta messages at the top of the message loop**

Edit `cli.py`'s `output_loop`. The current message loop body starts with:

```python
async for raw in ws:
    data = json.loads(raw)

    stop_spinner()

    # Welcome message with model info
    if "model" in data:
        ...
```

Add delta handling between `stop_spinner()` and the welcome check:

```python
async for raw in ws:
    data = json.loads(raw)

    stop_spinner()

    # Streaming delta — append to buffer and update Live region
    if data.get("delta"):
        chunk = data.get("content") or ""
        if stream_live is None:
            stream_buffer.clear()
            stream_live = Live(
                Text(""),
                console=console,
                transient=True,
                refresh_per_second=20,
            )
            stream_live.start()
        stream_buffer.append(chunk)
        stream_live.update(Text("".join(stream_buffer)))
        continue

    # Welcome message with model info
    if "model" in data:
        ...
```

- [ ] **Step 4: Flush before rendering tool_calls / content / final**

Edit `cli.py`'s `output_loop`. After the welcome-message handling and before the tool_calls/content rendering, flush the stream and capture what was streamed. The current block:

```python
                tool_calls = data.get("tool_calls") or []
                content = data.get("content") or ""
                final = data.get("final", False)
                attachments = data.get("attachments") or []

                if verbose and tool_calls:
                    for tc in tool_calls:
                        ...

                if content:
                    if verbose:
                        flush_tool_calls()
                    console.print(Markdown(content))
```

becomes:

```python
                tool_calls = data.get("tool_calls") or []
                content = data.get("content") or ""
                final = data.get("final", False)
                attachments = data.get("attachments") or []

                # Flush any accumulated stream first; render it as Markdown.
                streamed_text = flush_stream()
                if streamed_text.strip():
                    console.print(Markdown(streamed_text))

                if verbose and tool_calls:
                    for tc in tool_calls:
                        pending_tool_calls.append(tc)
                        line = Text()
                        line.append("  \u251c\u2500 ", style="dim")
                        line.append(tc)
                        console.print(line)

                if content and not streamed_text:
                    if verbose:
                        flush_tool_calls()
                    console.print(Markdown(content))
```

The key change: skip `console.print(Markdown(content))` when `streamed_text` is non-empty, because the streamed buffer already covered the same final answer.

- [ ] **Step 5: Handle stream cleanup on disconnect**

Edit `cli.py`'s `output_loop`. The current `finally`:

```python
        finally:
            # Unblock the input loop if the connection dropped before final:true
            ready.set()
```

Becomes:

```python
        finally:
            # Stop any in-flight Live region so the terminal isn't left
            # in a partial render state if the connection drops.
            if stream_live is not None:
                stream_live.stop()
                stream_live = None
                stream_buffer.clear()
            # Unblock the input loop if the connection dropped before final:true
            ready.set()
```

- [ ] **Step 6: Run the full test suite to confirm nothing else broke**

Run: `pytest tests/ -x -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add cli.py
git commit -m "feat: render streaming deltas in CLI via transient Live region"
```

---

## Task 7: Manual end-to-end verification

This task has no test code — it's a hands-on smoke test of the entire streaming path against a real LLM.

- [ ] **Step 1: Confirm `.env` has a working `MODEL` and API key**

Run: `grep -E '^(MODEL|ANTHROPIC_API_KEY|OPENAI_API_KEY|OPENROUTER_API_KEY)=' .env`

Expected: at least `MODEL` and one matching API key. If missing, set them up before continuing.

- [ ] **Step 2: Start the server**

Run in one terminal: `python run.py`

Expected: server listens on `ws://0.0.0.0:8765` (look for "WebSocket server listening on" in logs).

- [ ] **Step 3: Connect the CLI**

Run in a second terminal: `python cli.py --host localhost`

Expected: the welcome message ("model: ...") appears, then a `> ` prompt.

- [ ] **Step 4: Send a prompt that produces a long, plain-text response**

Type: `Write a 200-word explanation of how a B-tree works.`

Expected:
- Text appears incrementally, token-by-token, in plain (unformatted) style.
- When generation completes, the plain-text region is replaced with a properly Markdown-rendered version.
- The final stats line (`prompt: ... | completion: ... | ... tok/s | ... iter | ... wall`) prints below.

- [ ] **Step 5: Send a prompt that requires tool use**

Type: `read run.py and tell me what its main() function does`

Expected:
- An intermediate preamble (e.g., "I'll read the file…") streams in plain text.
- When the model calls `read`, the streaming region is flushed and re-rendered as Markdown.
- The tool-call line (`├─ Read run.py`) appears below it.
- The final answer streams in plain text, then re-renders as Markdown.
- Stats line follows.

- [ ] **Step 6: Send a prompt that produces no tool use and short output**

Type: `What's 2 + 2?`

Expected: a short streamed response followed by Markdown re-render and stats.

- [ ] **Step 7: Test reset during stream**

Type a long prompt, then while text is still streaming, press Ctrl-C.

Expected: clean exit; no terminal corruption.

- [ ] **Step 8: If all six checks pass, mark the task done**

If anything misbehaves (flicker, double-printing, missing markdown, empty output, terminal corruption), debug before continuing.

- [ ] **Step 9: Commit any tweaks needed during manual verification**

If the manual run revealed bugs that required additional code changes:

```bash
git add <changed files>
git commit -m "fix: <description of the issue>"
```

---

## Task 8: Update issue and prepare for merge

- [ ] **Step 1: Run the full test suite one final time**

Run: `pytest tests/ -q`
Expected: All tests PASS.

- [ ] **Step 2: Push the branch and open / update a PR**

Run:

```bash
git push -u origin HEAD
gh pr create --title "feat: stream agent responses to the CLI" --body "$(cat <<'EOF'
## Summary

Implements #24 — the CLI now streams assistant text as it's generated instead of waiting for full agent turns to complete.

- `call_llm` gains an optional `on_text_delta` callback; when provided, switches to LiteLLM streaming.
- `Agent.handle` and `agent_worker` thread the callback through.
- `OutgoingMessage` gets a new `delta` field; the WS payload includes a matching key.
- The CLI accumulates deltas in a `rich.live.Live(transient=True)` region rendered as plain text, then replaces it with a Markdown render once the stream completes.
- Email channel is untouched.

Spec: `docs/superpowers/specs/2026-04-20-cli-streaming-design.md`
Plan: `docs/superpowers/plans/2026-04-20-cli-streaming.md`

## Test plan

- [x] Unit tests for streaming `call_llm` (text deltas, tool-call accumulation, usage)
- [x] Unit tests for `OutgoingMessage.delta` and WS payload
- [x] Unit tests for `Agent.handle` forwarding `on_text_delta` to `call_llm`
- [x] Manual end-to-end smoke test (long text, tool use, short text, Ctrl-C during stream)
EOF
)"
```

Expected: PR URL printed.

- [ ] **Step 3: Comment on issue #24 with the PR link**

Run:

```bash
gh issue comment 24 --body "Implementation in progress: <PR URL from previous step>"
```

---

## Self-Review Notes

- **Spec coverage:** Wire protocol (Task 1, 2), `call_llm` streaming (Task 3), `Agent.handle` plumbing (Task 4), `agent_worker` callback (Task 5), CLI rendering with Live + double-print avoidance (Task 6), email channel untouched (no task — verified by absence of changes to `src/channels/email.py`), spinner gap (Task 6 — `stop_spinner()` already runs at top of message loop).
- **Placeholder scan:** None.
- **Type consistency:** `on_text_delta: Callable[[str], Awaitable[None]] | None` is consistent across `call_llm`, `Agent.handle`, and `agent_worker`. The callback signature `async def cb(text: str) -> None` is consistent everywhere.
