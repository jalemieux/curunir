# Delegate Tool Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `delegate` tool that spawns sub-agents with clean context windows, so tasks like document summarization and web research don't pollute the parent agent's conversation history.

**Architecture:** The `delegate` tool creates a fresh `Agent` instance, runs it to completion in an isolated session, and returns only the final text response. The sub-agent has access to all existing tools (read, write, bash, etc.) but NOT the delegate tool itself (no recursive spawning). Image attachments referenced in the task are base64-encoded and passed as multimodal content blocks to the sub-agent's first message. The parent agent sees the image-handling as a delegated task flow: channel saves image to disk → parent delegates analysis → sub-agent receives base64 → parent gets text description back.

**Tech Stack:** Python 3.12, asyncio, litellm, base64

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/tools/delegate.py` | Create | `exec_delegate()` — spawn sub-agent, handle image inlining, return result |
| `src/tools/schemas.py` | Modify | Add `delegate` tool schema to `get_tool_schemas()` |
| `src/tools/dispatcher.py` | Modify | Register `delegate` executor |
| `src/agent/agent.py` | Modify | Accept `exclude_tools` param so sub-agents can't delegate recursively |
| `run.py` | Modify | Remove image base64 inlining from `_build_content()` |
| `tests/test_delegate.py` | Create | Tests for delegate tool |
| `tests/test_agent.py` | Modify | Test `exclude_tools` filtering |
| `tests/test_schemas.py` | Modify | Update schema count and name set for new tool |

**Design notes:**
- The delegate executor is `async` because it awaits `agent.handle()`. The current dispatcher runs tool executors via `asyncio.to_thread()` (sync). The delegate tool is the first async tool — the dispatcher needs a small change to detect and await async executors directly.
- Sub-agents inherit the parent's `AgentConfig` (including system prompt/identity and `max_iterations=15`). This is intentional — the sub-agent should have the same capabilities and persona.
- Sub-agents do NOT receive an `on_tool_call` callback, so their intermediate tool calls are silent to the parent's UI. The parent only sees the final result.

---

## Chunk 1: Async-Aware Dispatcher and Tool Exclusion

### Task 1: Make dispatcher support async executors

The current dispatcher calls all tools via `asyncio.to_thread()` in `agent.py:50`. We need delegate to run async (it awaits `agent.handle()`). Rather than changing every call site, we mark async executors and let `agent.py` handle them differently.

**Files:**
- Modify: `src/tools/dispatcher.py`
- Modify: `src/agent/agent.py:50-55`
- Test: `tests/test_dispatcher.py`
- Test: `tests/test_agent.py`

- [ ] **Step 1: Write failing test for async executor dispatch**

In `tests/test_dispatcher.py`, add:

```python
import asyncio

class TestAsyncDispatch:
    def test_is_async_executor_false_for_sync(self):
        from src.tools.dispatcher import is_async_executor
        assert is_async_executor("bash") is False

    def test_is_async_executor_true_for_async(self):
        from src.tools.dispatcher import is_async_executor
        # Will be true once delegate is registered
        assert is_async_executor("delegate") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dispatcher.py::TestAsyncDispatch -v`
Expected: FAIL — `is_async_executor` doesn't exist yet

- [ ] **Step 3: Add `is_async_executor()` to dispatcher**

In `src/tools/dispatcher.py`, add:

```python
import asyncio

ASYNC_EXECUTORS: set[str] = set()

def is_async_executor(name: str) -> bool:
    """Check if a tool executor is async (needs await, not to_thread)."""
    return name.lower() in ASYNC_EXECUTORS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dispatcher.py::TestAsyncDispatch -v`
Expected: `test_is_async_executor_false_for_sync` PASSES, `test_is_async_executor_true_for_async` FAILS (delegate not registered yet — that's fine, we'll fix it in Task 3)

- [ ] **Step 5: Update agent.py to handle async executors**

In `src/agent/agent.py`, change the tool execution block (lines 50-55) from:

```python
result = await asyncio.to_thread(
    execute_tool_call,
    name,
    json.loads(args_str),
    self.config,
)
```

to:

```python
if is_async_executor(name):
    result = await execute_tool_call_async(
        name,
        json.loads(args_str),
        self.config,
    )
else:
    result = await asyncio.to_thread(
        execute_tool_call,
        name,
        json.loads(args_str),
        self.config,
    )
```

Add imports at top of `agent.py`:

```python
from src.tools.dispatcher import execute_tool_call, execute_tool_call_async, is_async_executor
```

- [ ] **Step 6: Add `execute_tool_call_async()` to dispatcher**

In `src/tools/dispatcher.py`:

```python
async def execute_tool_call_async(name: str, args: dict, config: AgentConfig) -> str:
    """Dispatch an async tool call."""
    executor = ASYNC_EXECUTORS_MAP.get(name.lower())
    if not executor:
        return f"Unknown async tool: {name}"
    return await executor(args, config)

ASYNC_EXECUTORS_MAP: dict = {}
```

- [ ] **Step 7: Write test for async dispatch in agent**

In `tests/test_agent.py`, add:

```python
class TestAsyncToolExecution:
    async def test_calls_async_executor_directly(self, agent):
        """Async tools should be awaited, not run via to_thread."""
        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_async",
                "type": "function",
                "function": {"name": "delegate", "arguments": json.dumps({"task": "say hello"})},
            }],
        )
        text_response = LLMResponse(text="Done", tool_calls=None)

        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[tool_response, text_response]), \
             patch("src.agent.agent.is_async_executor", return_value=True), \
             patch("src.agent.agent.execute_tool_call_async", new_callable=AsyncMock, return_value="sub-agent result"):
            result = await agent.handle("delegate this", "s1")

        assert result == "Done"
```

- [ ] **Step 8: Run all tests**

Run: `pytest tests/test_agent.py tests/test_dispatcher.py -v`
Expected: All pass

- [ ] **Step 9: Commit**

```bash
git add src/tools/dispatcher.py src/agent/agent.py tests/test_dispatcher.py tests/test_agent.py
git commit -m "feat: async-aware tool dispatcher for delegate support"
```

---

### Task 2: Add tool exclusion to Agent

Sub-agents must not have access to `delegate` (no recursive spawning). Add an `exclude_tools` param that filters tool schemas and blocks execution.

**Files:**
- Modify: `src/agent/agent.py`
- Test: `tests/test_agent.py`

- [ ] **Step 1: Write failing test for tool exclusion**

In `tests/test_agent.py`, add:

```python
class TestToolExclusion:
    async def test_excluded_tools_not_in_schemas(self, agent_config):
        agent = Agent(agent_config, exclude_tools={"bash"})
        # The agent should filter schemas passed to call_llm
        mock_response = LLMResponse(text="Hi", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await agent.handle("hello", "s1")
        schemas = mock_llm.call_args[0][2]  # third positional arg
        tool_names = [s["function"]["name"] for s in schemas]
        assert "bash" not in tool_names

    async def test_excluded_tool_call_rejected(self, agent_config):
        agent = Agent(agent_config, exclude_tools={"bash"})
        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_blocked",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "echo no"})},
            }],
        )
        text_response = LLMResponse(text="Ok", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[tool_response, text_response]):
            result = await agent.handle("run bash", "s1")
        # Tool result should indicate the tool is not available
        history = agent.sessions["s1"]
        tool_msg = [m for m in history if m["role"] == "tool"][0]
        assert "not available" in tool_msg["content"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent.py::TestToolExclusion -v`
Expected: FAIL — `Agent.__init__()` doesn't accept `exclude_tools`

- [ ] **Step 3: Implement tool exclusion in Agent**

In `src/agent/agent.py`, modify `__init__` and the tool loop:

```python
class Agent:
    def __init__(self, config: AgentConfig, exclude_tools: set[str] | None = None):
        self.config = config
        self.sessions: dict[str, list[dict]] = {}
        self.static_prompt = build_static_prompt(config)
        self.exclude_tools = exclude_tools or set()

    def _get_tool_schemas(self) -> list[dict]:
        schemas = get_tool_schemas()
        if self.exclude_tools:
            schemas = [s for s in schemas if s["function"]["name"] not in self.exclude_tools]
        return schemas
```

In `handle()`, replace `get_tool_schemas()` call with `self._get_tool_schemas()`.

Before executing a tool call, add a guard:

```python
if name.lower() in self.exclude_tools:
    history.append({
        "role": "tool",
        "tool_call_id": tool_call["id"],
        "content": f"Tool '{name}' is not available in this context.",
    })
    continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent.py::TestToolExclusion -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: All pass (existing tests unaffected since `exclude_tools` defaults to empty set)

- [ ] **Step 6: Commit**

```bash
git add src/agent/agent.py tests/test_agent.py
git commit -m "feat: add exclude_tools to Agent for sub-agent isolation"
```

---

## Chunk 2: Delegate Tool

### Task 3: Create the delegate tool

The core tool: spawns a sub-agent with clean context, optionally inlines images, returns only the final text.

**Files:**
- Create: `src/tools/delegate.py`
- Modify: `src/tools/schemas.py`
- Modify: `src/tools/dispatcher.py`
- Test: `tests/test_delegate.py`

- [ ] **Step 1: Write failing tests for delegate**

Create `tests/test_delegate.py`:

```python
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.llm import LLMResponse


class TestDelegate:
    async def test_returns_sub_agent_response(self, agent_config):
        from src.tools.delegate import exec_delegate

        mock_response = LLMResponse(text="Summary: doc is about X", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await exec_delegate(
                {"task": "Summarize the document"},
                agent_config,
            )
        assert "Summary" in result

    async def test_sub_agent_cannot_delegate(self, agent_config):
        """Sub-agents must not have the delegate tool available."""
        from src.tools.delegate import exec_delegate

        mock_response = LLMResponse(text="Done", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await exec_delegate({"task": "do something"}, agent_config)

        # Check schemas passed to call_llm don't include delegate
        schemas = mock_llm.call_args[0][2]
        tool_names = [s["function"]["name"] for s in schemas]
        assert "delegate" not in tool_names

    async def test_image_paths_inlined_as_base64(self, agent_config, tmp_path):
        from src.tools.delegate import exec_delegate

        # Create a tiny 1x1 PNG
        import base64
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        img_path = tmp_path / "test.png"
        img_path.write_bytes(png_bytes)

        mock_response = LLMResponse(text="It's a white pixel", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await exec_delegate(
                {"task": "Describe the image", "image_paths": [str(img_path)]},
                agent_config,
            )

        # The first message should have multimodal content blocks
        messages = mock_llm.call_args[0][1]
        user_msg = [m for m in messages if m["role"] == "user"][0]
        assert isinstance(user_msg["content"], list)
        assert any(b.get("type") == "image_url" for b in user_msg["content"])

    async def test_max_iterations_respected(self, agent_config):
        from src.tools.delegate import exec_delegate

        agent_config.max_iterations = 2
        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_loop",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "echo loop"})},
            }],
        )
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=tool_response):
            result = await exec_delegate({"task": "loop"}, agent_config)
        assert "iteration limit" in result.lower()

    async def test_empty_task_returns_error(self, agent_config):
        from src.tools.delegate import exec_delegate
        result = await exec_delegate({"task": ""}, agent_config)
        assert "error" in result.lower()

    async def test_invalid_image_paths_type_handled(self, agent_config):
        """If LLM sends image_paths as a string instead of list, handle gracefully."""
        from src.tools.delegate import exec_delegate

        mock_response = LLMResponse(text="Done", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response):
            # Should not crash even with wrong type
            result = await exec_delegate(
                {"task": "describe", "image_paths": "/tmp/img.png"},
                agent_config,
            )
        assert isinstance(result, str)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_delegate.py -v`
Expected: FAIL — `src.tools.delegate` doesn't exist

- [ ] **Step 3: Implement `exec_delegate()`**

Create `src/tools/delegate.py`:

```python
# src/tools/delegate.py
import asyncio
import base64
import logging
import mimetypes
from uuid import uuid4

from src.agent.agent import Agent
from src.config import AgentConfig

logger = logging.getLogger(__name__)

# Tools the sub-agent cannot use (prevent recursive delegation)
_EXCLUDED_TOOLS = {"delegate"}

# Sub-agent timeout in seconds
_TIMEOUT = 300


async def exec_delegate(args: dict, config: AgentConfig) -> str:
    """Spawn a sub-agent with a clean context window and return its response."""
    task = args.get("task", "")
    if not task:
        return "Error: 'task' is required"

    image_paths = args.get("image_paths", [])
    # Guard against LLM sending a string instead of a list
    if isinstance(image_paths, str):
        image_paths = [image_paths]

    # Build the sub-agent's input: text, or multimodal blocks if images
    if image_paths:
        content = _build_multimodal_content(task, image_paths)
    else:
        content = task

    sub_agent = Agent(config, exclude_tools=_EXCLUDED_TOOLS)
    session_id = str(uuid4())

    logger.info("Spawning sub-agent %s: %.80s", session_id[:8], task)
    try:
        result = await asyncio.wait_for(
            sub_agent.handle(content, session_id),
            timeout=_TIMEOUT,
        )
        logger.info("Sub-agent %s completed", session_id[:8])
        return result
    except asyncio.TimeoutError:
        logger.warning("Sub-agent %s timed out after %ds", session_id[:8], _TIMEOUT)
        return f"Sub-agent timed out after {_TIMEOUT}s"
    except Exception as e:
        logger.error("Sub-agent %s failed: %s", session_id[:8], e)
        return f"Sub-agent error: {e}"


def _build_multimodal_content(task: str, image_paths: list[str]) -> list[dict]:
    """Build multimodal content blocks with base64-encoded images."""
    blocks: list[dict] = [{"type": "text", "text": task}]

    for path in image_paths:
        try:
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            mime = mimetypes.guess_type(path)[0] or "image/png"
            blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{data}"},
            })
        except Exception:
            blocks.append({"type": "text", "text": f"(Could not read image: {path})"})

    return blocks
```

- [ ] **Step 4: Run delegate tests**

Run: `pytest tests/test_delegate.py -v`
Expected: PASS

- [ ] **Step 5: Add delegate schema to `schemas.py`**

In `src/tools/schemas.py`, add to the list in `get_tool_schemas()`:

```python
{
    "type": "function",
    "function": {
        "name": "delegate",
        "description": (
            "Delegate a task to a sub-agent with a clean context window. "
            "Use this for tasks that involve processing large documents, "
            "analyzing images, or doing multi-step research. The sub-agent "
            "has access to all tools (read, write, bash, etc.) but runs in "
            "isolation — its intermediate work won't fill up your context. "
            "You get back only the final answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Clear description of what the sub-agent should do.",
                },
                "image_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of image file paths to include for visual analysis.",
                },
            },
            "required": ["task"],
        },
    },
},
```

- [ ] **Step 6: Register delegate in dispatcher**

In `src/tools/dispatcher.py`:

```python
from src.tools.delegate import exec_delegate

ASYNC_EXECUTORS_MAP = {
    "delegate": exec_delegate,
}

ASYNC_EXECUTORS = set(ASYNC_EXECUTORS_MAP.keys())
```

- [ ] **Step 7: Update existing schema tests**

In `tests/test_schemas.py`, update the count and name set:

```python
def test_returns_eight_schemas():
    schemas = get_tool_schemas()
    assert len(schemas) == 8


def test_expected_tool_names():
    names = {s["function"]["name"] for s in get_tool_schemas()}
    assert names == {"glob", "grep", "read", "edit", "write", "bash", "load_skill", "delegate"}
```

- [ ] **Step 8: Run full test suite**

Run: `pytest tests/ -v`
Expected: All pass, including the `test_is_async_executor_true_for_async` test from Task 1

- [ ] **Step 9: Commit**

```bash
git add src/tools/delegate.py src/tools/schemas.py src/tools/dispatcher.py tests/test_delegate.py tests/test_schemas.py
git commit -m "feat: add delegate tool for sub-agent context isolation"
```

---

## Chunk 3: Stop Inlining Images in Parent Context

### Task 4: Remove image inlining from `_build_content()`

Images should no longer be base64-encoded into the parent's context. The parent sees the file path and can delegate analysis to a sub-agent.

**Files:**
- Modify: `run.py:49-75`
- Modify: `tests/test_email_channel.py` (if image inlining is tested there)

- [ ] **Step 1: Write test for new behavior**

The `_build_content` function should now always return `msg.content` as-is (text string), since image paths are already noted in the content by the email channel.

```python
# In a test file or inline verification:
# _build_content should no longer produce multimodal blocks
```

- [ ] **Step 2: Simplify `_build_content()` in `run.py`**

Replace lines 49-75 in `run.py`:

```python
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def _build_content(msg) -> str | list:
    """Build LLM content from a message, inlining image attachments as base64."""
    if not msg.attachments:
        return msg.content

    images = [att for att in msg.attachments if att.get("mime_type") in _IMAGE_MIMES]
    if not images:
        return msg.content

    blocks: list[dict] = [{"type": "text", "text": msg.content}]
    for img in images:
        # ... base64 encoding ...
```

with:

```python
def _build_content(msg) -> str:
    """Build LLM content from a message.

    Attachments are referenced by file path in msg.content (added by the
    channel). The agent uses the delegate tool to analyze images and
    large documents in a sub-agent with a clean context window.
    """
    return msg.content
```

- [ ] **Step 3: Clean up unused imports**

Remove `import base64` from `run.py` (and `_IMAGE_MIMES` constant).

- [ ] **Step 4: Update `_summarize_tool_call` for delegate**

In `run.py`, add a case to `_summarize_tool_call`:

```python
case "delegate":
    task = args.get("task", "")
    if len(task) > 60:
        task = task[:57] + "..."
    return f"Delegate: {task}"
```

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: All pass. `_build_content` is not directly tested anywhere — it's an internal function in `run.py`. The email channel tests cover attachment metadata in `msg.content` and `msg.attachments`, which are unaffected.

- [ ] **Step 6: Commit**

```bash
git add run.py
git commit -m "refactor: stop inlining images, delegate to sub-agent instead"
```

---

## Chunk 4: Safety Net — Context Window Truncation

### Task 5: Add basic history truncation as a fallback

Even with delegation, the parent context can still grow too large over very long sessions. Add a simple token-estimation guard that trims the oldest messages when approaching the limit.

**Files:**
- Modify: `src/agent/agent.py`
- Test: `tests/test_agent.py`

- [ ] **Step 1: Write failing test for truncation**

```python
class TestHistoryTruncation:
    async def test_trims_old_messages_when_over_limit(self, agent_config):
        agent = Agent(agent_config)
        session_id = "s-trunc"
        history = agent.sessions.setdefault(session_id, [])
        for i in range(100):
            history.append({"role": "user", "content": "x" * 10_000})
            history.append({"role": "assistant", "content": "y" * 10_000})

        mock_response = LLMResponse(text="ok", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await agent.handle("new message", "s-trunc")

        messages = mock_llm.call_args[0][1]
        # Should be trimmed (system + trimmed history + new user msg)
        assert len(messages) < 202

    async def test_truncation_preserves_message_pairs(self, agent_config):
        """Truncation should not leave orphaned tool results or split pairs."""
        agent = Agent(agent_config)
        session_id = "s-pairs"
        history = agent.sessions.setdefault(session_id, [])
        # Add user/assistant pairs and a tool_call/tool group
        for i in range(50):
            history.append({"role": "user", "content": "x" * 20_000})
            history.append({"role": "assistant", "content": "y" * 20_000})

        mock_response = LLMResponse(text="ok", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await agent.handle("new message", "s-pairs")

        messages = mock_llm.call_args[0][1]
        # After system prompt, first message should be "user" (not orphaned assistant/tool)
        non_system = [m for m in messages if m["role"] != "system"]
        assert non_system[0]["role"] == "user"
```

- [ ] **Step 2: Run test to verify baseline**

Run: `pytest tests/test_agent.py::TestHistoryTruncation -v`
Expected: FAIL — no truncation happening

- [ ] **Step 3: Implement `_trim_history()` in Agent**

In `src/agent/agent.py`, add a module-level helper and call it in `handle()`:

```python
_MAX_HISTORY_CHARS = 600_000  # ~150k tokens, leaves room for system prompt + response


def _estimate_chars(messages: list[dict]) -> int:
    """Rough character count across all message contents."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for block in content:
                total += len(str(block))
    return total


def _trim_history(history: list[dict], max_chars: int = _MAX_HISTORY_CHARS) -> None:
    """Remove oldest messages in coherent groups until under the char limit.

    Groups: user+assistant pairs, or assistant(tool_calls)+tool+...+tool sequences.
    Always removes from the front so the most recent context is preserved.
    After trimming, the first message should be role=user.
    """
    while len(history) > 2 and _estimate_chars(history) > max_chars:
        # Remove messages from the front until we hit the next "user" message
        history.pop(0)
        while history and history[0]["role"] != "user":
            history.pop(0)
```

In `handle()`, before `messages = [{"role": "system", ...}] + history`:

```python
_trim_history(history)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent.py::TestHistoryTruncation -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/agent/agent.py tests/test_agent.py
git commit -m "feat: add history truncation as context window safety net"
```
