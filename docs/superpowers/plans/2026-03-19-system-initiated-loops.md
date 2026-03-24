# System-Initiated Agentic Loops Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Curunir's agentic loop to run autonomously from scheduled system prompts, with no user message required.

**Architecture:** A `schedule` tool gives the agent CRUD control over `context/schedules.json`. A background async scheduler checks every 60 seconds for due tasks and fires them via a new `system_task_prompt` parameter on `agent.handle()`, which skips the user-message step and weaves the task prompt into the system prompt.

**Tech Stack:** Python asyncio, croniter (new dependency)

**Spec:** `docs/superpowers/specs/2026-03-19-system-initiated-loops-design.md`

---

### Task 1: Add croniter dependency

**Files:**
- Modify: `requirements.txt:8`

- [ ] **Step 1: Add croniter to requirements.txt**

Add `croniter` after the existing dependencies:

```
croniter
```

- [ ] **Step 2: Install and verify**

Run: `pip install croniter`
Expected: successful install

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add croniter dependency for task scheduling"
```

---

### Task 2: Schedule tool — CRUD operations

**Files:**
- Create: `src/tools/schedule_tool.py`
- Test: `tests/test_schedule_tool.py`

The schedule tool reads/writes `context/schedules.json` (path resolved from `config.context_dir`). Writes use atomic temp-file-then-rename to prevent partial reads from the scheduler.

- [ ] **Step 1: Write failing tests**

Create `tests/test_schedule_tool.py`:

```python
# tests/test_schedule_tool.py
import json

import pytest

from src.tools.schedule_tool import exec_schedule


@pytest.fixture
def schedule_file(tmp_path, agent_config):
    """Point agent_config.context_dir at tmp_path with an empty schedules.json."""
    sf = tmp_path / "schedules.json"
    sf.write_text("[]")
    agent_config.context_dir = tmp_path
    return sf


class TestScheduleAdd:
    def test_add_task(self, agent_config, schedule_file):
        result = exec_schedule({
            "action": "add",
            "id": "morning-brief",
            "cron": "0 9 * * *",
            "prompt": "Check GitHub notifications.",
        }, agent_config)
        assert "added" in result.lower()
        tasks = json.loads(schedule_file.read_text())
        assert len(tasks) == 1
        assert tasks[0]["id"] == "morning-brief"
        assert tasks[0]["cron"] == "0 9 * * *"
        assert tasks[0]["prompt"] == "Check GitHub notifications."
        assert tasks[0]["skill"] is None
        assert tasks[0]["enabled"] is True
        assert tasks[0]["last_run"] == 0

    def test_add_with_skill(self, agent_config, schedule_file):
        result = exec_schedule({
            "action": "add",
            "id": "pr-review",
            "cron": "*/30 * * * *",
            "prompt": "Review open PRs.",
            "skill": "deep-research",
        }, agent_config)
        tasks = json.loads(schedule_file.read_text())
        assert tasks[0]["skill"] == "deep-research"

    def test_add_duplicate_id_fails(self, agent_config, schedule_file):
        exec_schedule({"action": "add", "id": "t1", "cron": "* * * * *", "prompt": "p"}, agent_config)
        result = exec_schedule({"action": "add", "id": "t1", "cron": "* * * * *", "prompt": "p2"}, agent_config)
        assert "already exists" in result.lower()

    def test_add_invalid_cron_fails(self, agent_config, schedule_file):
        result = exec_schedule({
            "action": "add", "id": "bad", "cron": "not a cron", "prompt": "p",
        }, agent_config)
        assert "invalid" in result.lower()

    def test_add_missing_fields_fails(self, agent_config, schedule_file):
        result = exec_schedule({"action": "add", "id": "t1"}, agent_config)
        assert "missing" in result.lower() or "required" in result.lower()


class TestScheduleList:
    def test_list_empty(self, agent_config, schedule_file):
        result = exec_schedule({"action": "list"}, agent_config)
        assert "no scheduled tasks" in result.lower()

    def test_list_with_tasks(self, agent_config, schedule_file):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p1"}, agent_config)
        exec_schedule({"action": "add", "id": "t2", "cron": "0 17 * * *", "prompt": "p2"}, agent_config)
        result = exec_schedule({"action": "list"}, agent_config)
        assert "t1" in result
        assert "t2" in result


class TestScheduleUpdate:
    def test_update_cron(self, agent_config, schedule_file):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        result = exec_schedule({"action": "update", "id": "t1", "cron": "0 10 * * *"}, agent_config)
        assert "updated" in result.lower()
        tasks = json.loads(schedule_file.read_text())
        assert tasks[0]["cron"] == "0 10 * * *"

    def test_update_prompt(self, agent_config, schedule_file):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "old"}, agent_config)
        exec_schedule({"action": "update", "id": "t1", "prompt": "new"}, agent_config)
        tasks = json.loads(schedule_file.read_text())
        assert tasks[0]["prompt"] == "new"

    def test_update_enabled(self, agent_config, schedule_file):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        exec_schedule({"action": "update", "id": "t1", "enabled": False}, agent_config)
        tasks = json.loads(schedule_file.read_text())
        assert tasks[0]["enabled"] is False

    def test_update_nonexistent_fails(self, agent_config, schedule_file):
        result = exec_schedule({"action": "update", "id": "nope", "cron": "* * * * *"}, agent_config)
        assert "not found" in result.lower()

    def test_update_invalid_cron_fails(self, agent_config, schedule_file):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        result = exec_schedule({"action": "update", "id": "t1", "cron": "bad"}, agent_config)
        assert "invalid" in result.lower()


class TestScheduleRemove:
    def test_remove_task(self, agent_config, schedule_file):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        result = exec_schedule({"action": "remove", "id": "t1"}, agent_config)
        assert "removed" in result.lower()
        tasks = json.loads(schedule_file.read_text())
        assert len(tasks) == 0

    def test_remove_nonexistent_fails(self, agent_config, schedule_file):
        result = exec_schedule({"action": "remove", "id": "nope"}, agent_config)
        assert "not found" in result.lower()


class TestScheduleFileCreation:
    def test_creates_file_if_missing(self, tmp_path, agent_config):
        agent_config.context_dir = tmp_path
        # No schedules.json exists yet
        result = exec_schedule({"action": "list"}, agent_config)
        assert "no scheduled tasks" in result.lower()

    def test_add_creates_file_if_missing(self, tmp_path, agent_config):
        agent_config.context_dir = tmp_path
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        sf = tmp_path / "schedules.json"
        assert sf.exists()
        assert len(json.loads(sf.read_text())) == 1


class TestInvalidAction:
    def test_unknown_action(self, agent_config, schedule_file):
        result = exec_schedule({"action": "bogus"}, agent_config)
        assert "unknown" in result.lower() or "invalid" in result.lower()

    def test_missing_action(self, agent_config, schedule_file):
        result = exec_schedule({}, agent_config)
        assert "action" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_schedule_tool.py -v`
Expected: FAIL — `ImportError: cannot import name 'exec_schedule'`

- [ ] **Step 3: Implement the schedule tool**

Create `src/tools/schedule_tool.py`:

```python
# src/tools/schedule_tool.py
"""CRUD operations for scheduled tasks stored in context/schedules.json."""

import json
import os
import tempfile
from pathlib import Path

from croniter import croniter

from src.config import AgentConfig


def _schedule_path(config: AgentConfig) -> Path:
    return config.context_dir / "schedules.json"


def _load(config: AgentConfig) -> list[dict]:
    path = _schedule_path(config)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _save(config: AgentConfig, tasks: list[dict]) -> None:
    path = _schedule_path(config)
    # Atomic write: temp file + rename to prevent partial reads
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(tasks, f, indent=2)
        os.rename(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _validate_cron(expr: str) -> bool:
    try:
        croniter(expr)
        return True
    except (ValueError, KeyError):
        return False


def exec_schedule(args: dict, config: AgentConfig) -> str:
    action = args.get("action")
    if not action:
        return "Error: missing 'action' field. Use: list, add, update, remove."

    match action:
        case "list":
            return _list(config)
        case "add":
            return _add(args, config)
        case "update":
            return _update(args, config)
        case "remove":
            return _remove(args, config)
        case _:
            return f"Error: unknown action '{action}'. Use: list, add, update, remove."


def _list(config: AgentConfig) -> str:
    tasks = _load(config)
    if not tasks:
        return "No scheduled tasks."
    lines = []
    for t in tasks:
        status = "enabled" if t.get("enabled", True) else "disabled"
        skill = f" (skill: {t['skill']})" if t.get("skill") else ""
        lines.append(f"- **{t['id']}** `{t['cron']}` [{status}]{skill}\n  {t['prompt']}")
    return "\n".join(lines)


def _add(args: dict, config: AgentConfig) -> str:
    task_id = args.get("id")
    cron = args.get("cron")
    prompt = args.get("prompt")

    if not task_id or not cron or not prompt:
        return "Error: 'add' requires 'id', 'cron', and 'prompt' fields."

    if not _validate_cron(cron):
        return f"Error: invalid cron expression '{cron}'."

    tasks = _load(config)
    if any(t["id"] == task_id for t in tasks):
        return f"Error: task '{task_id}' already exists. Use 'update' to modify it."

    tasks.append({
        "id": task_id,
        "cron": cron,
        "prompt": prompt,
        "skill": args.get("skill"),
        "enabled": True,
        "last_run": 0,
    })
    _save(config, tasks)
    return f"Task '{task_id}' added — scheduled at `{cron}`."


def _update(args: dict, config: AgentConfig) -> str:
    task_id = args.get("id")
    if not task_id:
        return "Error: 'update' requires 'id' field."

    tasks = _load(config)
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        return f"Error: task '{task_id}' not found."

    if "cron" in args:
        if not _validate_cron(args["cron"]):
            return f"Error: invalid cron expression '{args['cron']}'."
        task["cron"] = args["cron"]
    if "prompt" in args:
        task["prompt"] = args["prompt"]
    if "skill" in args:
        task["skill"] = args["skill"]
    if "enabled" in args:
        task["enabled"] = bool(args["enabled"])

    _save(config, tasks)
    return f"Task '{task_id}' updated."


def _remove(args: dict, config: AgentConfig) -> str:
    task_id = args.get("id")
    if not task_id:
        return "Error: 'remove' requires 'id' field."

    tasks = _load(config)
    original_len = len(tasks)
    tasks = [t for t in tasks if t["id"] != task_id]
    if len(tasks) == original_len:
        return f"Error: task '{task_id}' not found."

    _save(config, tasks)
    return f"Task '{task_id}' removed."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_schedule_tool.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/tools/schedule_tool.py tests/test_schedule_tool.py
git commit -m "feat: add schedule tool with CRUD operations"
```

---

### Task 3: Register schedule tool in schemas and dispatcher

**Files:**
- Modify: `src/tools/schemas.py:248` (after the last schema in `_SCHEMAS`)
- Modify: `src/tools/dispatcher.py:8-9` (import) and `dispatcher.py:19` (add to `_SYNC_EXECUTORS`)
- Modify: `tests/test_agent.py:146` (update tool count assertion)
- Test: `tests/test_dispatcher.py` (add dispatch test)

- [ ] **Step 1: Write failing test for dispatcher**

Add to `tests/test_dispatcher.py`:

```python
    async def test_dispatches_schedule(self, tmp_path, agent_config):
        agent_config.context_dir = tmp_path
        result = await execute_tool_call(
            "schedule", {"action": "list"}, agent_config,
        )
        assert "no scheduled tasks" in result.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dispatcher.py::TestExecuteToolCall::test_dispatches_schedule -v`
Expected: FAIL — `Unknown tool: schedule`

- [ ] **Step 3: Add schema to schemas.py**

Add to the `_SCHEMAS` list in `src/tools/schemas.py`, after the `delegate` schema (before the closing `]` on line 248):

```python
        {
            "type": "function",
            "function": {
                "name": "schedule",
                "description": (
                    "Manage scheduled tasks that run autonomously on a cron schedule. "
                    "Use this to set up recurring tasks like morning briefs, PR checks, "
                    "or maintenance jobs. Scheduled tasks run in their own session with "
                    "no conversation context, so make prompts self-contained."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["list", "add", "update", "remove"],
                            "description": "The operation to perform.",
                        },
                        "id": {
                            "type": "string",
                            "description": "Human-readable task ID (e.g. 'morning-brief'). Required for add/update/remove.",
                        },
                        "cron": {
                            "type": "string",
                            "description": "5-field cron expression (e.g. '0 9 * * *' for 9am daily). Required for add.",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "The instruction to execute when the task fires. Must be self-contained. Required for add.",
                        },
                        "skill": {
                            "type": "string",
                            "description": "Optional skill name to load before executing the prompt.",
                        },
                        "enabled": {
                            "type": "boolean",
                            "description": "Enable or disable the task. Used with update.",
                        },
                    },
                    "required": ["action"],
                },
            },
        },
```

- [ ] **Step 4: Add to dispatcher.py**

Add import at line 8 (with the other tool imports):

```python
from src.tools.schedule_tool import exec_schedule
```

Add to `_SYNC_EXECUTORS` dict:

```python
    "schedule": exec_schedule,
```

- [ ] **Step 5: Update tool count in test_agent.py**

In `tests/test_agent.py:146`, change the tool count assertion from 9 to 10:

```python
        assert len(schemas) == 10  # all tools including delegate, web_fetch, and schedule
```

- [ ] **Step 6: Run all tests**

Run: `pytest tests/test_dispatcher.py tests/test_agent.py tests/test_schedule_tool.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/tools/schemas.py src/tools/dispatcher.py tests/test_dispatcher.py tests/test_agent.py
git commit -m "feat: register schedule tool in schemas and dispatcher"
```

---

### Task 4: System-task mode in agent.handle()

**Files:**
- Modify: `src/agent/agent.py:131-146` (handle method signature and user message logic)
- Modify: `src/agent/agent.py:81-96` (`_trim_history` to handle no-user-message sessions)
- Test: `tests/test_agent.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_agent.py`:

```python
class TestSystemTaskMode:
    async def test_system_task_no_user_message_sent_to_llm(self, agent):
        mock_response = LLMResponse(text="Task done.", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            result = await agent.handle("", "sched:test:123", system_task_prompt="Do the thing.")
        assert result == "Task done."
        # Check via the LLM call: no user message should be in the messages list
        messages = mock_llm.call_args[0][1]
        non_system = [m for m in messages if m["role"] != "system"]
        assert not any(m["role"] == "user" for m in non_system)

    async def test_system_task_prompt_in_system_message(self, agent):
        mock_response = LLMResponse(text="Done", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response) as mock_llm:
            await agent.handle("", "sched:test:123", system_task_prompt="Check PRs.")
        messages = mock_llm.call_args[0][1]
        system_msg = messages[0]["content"]
        assert "## Scheduled Task" in system_msg
        assert "Check PRs." in system_msg

    async def test_system_task_cleans_up_session(self, agent):
        mock_response = LLMResponse(text="Done", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response):
            await agent.handle("", "sched:test:123", system_task_prompt="Do it.")
        # Session should be cleaned up after completion
        assert "sched:test:123" not in agent.sessions

    async def test_system_task_with_tool_calls(self, agent):
        tool_response = LLMResponse(
            text=None,
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "echo scheduled"})},
            }],
        )
        text_response = LLMResponse(text="Task complete.", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, side_effect=[tool_response, text_response]):
            result = await agent.handle("", "sched:test:456", system_task_prompt="Run a command.")
        assert result == "Task complete."
        # Session cleaned up after completion
        assert "sched:test:456" not in agent.sessions

    async def test_normal_handle_unchanged(self, agent):
        """Ensure regular user messages still work as before."""
        mock_response = LLMResponse(text="Hello!", tool_calls=None)
        with patch("src.agent.agent.call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await agent.handle("hi", "normal-session")
        assert result == "Hello!"
        history = agent.sessions["normal-session"]
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "hi"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agent.py::TestSystemTaskMode -v`
Expected: FAIL — `TypeError: handle() got an unexpected keyword argument 'system_task_prompt'`

- [ ] **Step 3: Modify agent.handle() to support system_task_prompt**

In `src/agent/agent.py`, modify the `handle` method:

Change the signature (line 131-134) to:

```python
    async def handle(
        self, message: str | list, session_id: str,
        on_tool_call=None, attachments: list[dict] | None = None,
        system_task_prompt: str | None = None,
    ) -> str:
```

Replace lines 145-148 (session init, user message append, system prompt build) with:

```python
        history = self.sessions.setdefault(session_id, [])

        if system_task_prompt:
            # System-initiated task: no user message, task prompt goes in system prompt
            system_prompt = (
                self.static_prompt
                + f"\n\nCurrent time: {datetime.now().isoformat()}"
                + f"\n\n## Scheduled Task\n{system_task_prompt}"
            )
        else:
            history.append({"role": "user", "content": message})
            system_prompt = self.static_prompt + f"\n\nCurrent time: {datetime.now().isoformat()}"
```

- [ ] **Step 4: Fix _trim_history for no-user-message sessions**

Replace `_trim_history` (lines 81-96) with:

```python
def _trim_history(history: list[dict], max_chars: int = _MAX_HISTORY_CHARS) -> None:
    """Remove oldest messages in coherent groups until under the char limit.

    Groups: user+assistant pairs, or assistant(tool_calls)+tool+...+tool sequences.
    Always removes from the front so the most recent context is preserved.
    Keeps at least one user message (if any exist) to avoid empty-messages API errors.
    For system-task sessions (no user messages), trims assistant+tool groups from the front.
    """
    user_count = sum(1 for m in history if m["role"] == "user")

    if user_count > 0:
        # Normal session: trim by user message groups, keep at least one
        while user_count > 1 and _estimate_chars(history) > max_chars:
            if history[0]["role"] == "user":
                user_count -= 1
            history.pop(0)
            while history and history[0]["role"] != "user":
                history.pop(0)
    else:
        # System-task session: trim assistant+tool groups from front
        while len(history) > 1 and _estimate_chars(history) > max_chars:
            # Remove one assistant+tool group from the front
            history.pop(0)
            while history and history[0]["role"] == "tool":
                history.pop(0)
```

- [ ] **Step 5: Add session cleanup after system task completes**

After the main loop in `handle()`, before each `return` statement (lines 226, 230, 233), add session cleanup for system tasks. The cleanest way: add a helper at the end of `handle()`. Replace the three return points at the end of the method (after the for loop, starting around line 223) with:

After line 222 (`continue`), the rest of the method becomes:

```python
            if response.text:
                logger.info("[%s] agent done after %d iteration(s), response length: %d chars", sid, iteration + 1, len(response.text))
                history.append({"role": "assistant", "content": response.text})
                if system_task_prompt:
                    self.sessions.pop(session_id, None)
                return response.text

            logger.warning("[%s] LLM returned empty response", sid)
            history.append({"role": "assistant", "content": ""})
            if system_task_prompt:
                self.sessions.pop(session_id, None)
            return "Error: LLM returned empty response."

        logger.warning("[%s] iteration limit reached (%d)", sid, self.config.max_iterations)
        if system_task_prompt:
            self.sessions.pop(session_id, None)
        return "Iteration limit reached."
```

- [ ] **Step 6: Run all agent tests**

Run: `pytest tests/test_agent.py -v`
Expected: all PASS (both new and existing tests)

- [ ] **Step 7: Commit**

```bash
git add src/agent/agent.py tests/test_agent.py
git commit -m "feat: add system_task_prompt mode to agent.handle()"
```

---

### Task 5: Async scheduler

**Files:**
- Create: `src/scheduler.py`
- Test: `tests/test_scheduler.py`

The scheduler wakes every 60 seconds, reads `context/schedules.json`, and fires due tasks. It uses `croniter` to check if a task was due since its `last_run` timestamp. It updates `last_run` in the file before spawning the task.

- [ ] **Step 1: Write failing tests**

Create `tests/test_scheduler.py`:

```python
# tests/test_scheduler.py
import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import pytest
from croniter import croniter

from src.scheduler import _check_and_fire, _update_last_run, _is_due


@pytest.fixture
def schedule_file(tmp_path, agent_config):
    agent_config.context_dir = tmp_path
    sf = tmp_path / "schedules.json"
    sf.write_text("[]")
    return sf


class TestIsDue:
    def test_never_run_task_is_due(self):
        task = {"cron": "* * * * *", "last_run": 0}
        assert _is_due(task, time.time()) is True

    def test_recently_run_task_not_due(self):
        task = {"cron": "0 9 * * *", "last_run": int(time.time())}
        assert _is_due(task, time.time()) is False

    def test_last_run_in_future_not_due(self):
        task = {"cron": "* * * * *", "last_run": int(time.time()) + 3600}
        assert _is_due(task, time.time()) is False

    def test_invalid_cron_not_due(self):
        task = {"cron": "not valid", "last_run": 0}
        assert _is_due(task, time.time()) is False

    def test_hourly_task_due_after_one_hour(self):
        now = time.time()
        task = {"cron": "0 * * * *", "last_run": int(now) - 3601}
        assert _is_due(task, now) is True


class TestUpdateLastRun:
    def test_updates_last_run(self, schedule_file, agent_config):
        schedule_file.write_text(json.dumps([
            {"id": "t1", "cron": "* * * * *", "prompt": "p", "skill": None, "enabled": True, "last_run": 0},
        ]))
        now = int(time.time())
        _update_last_run(agent_config, "t1", now)
        tasks = json.loads(schedule_file.read_text())
        assert tasks[0]["last_run"] == now

    def test_update_nonexistent_task_is_noop(self, schedule_file, agent_config):
        schedule_file.write_text(json.dumps([
            {"id": "t1", "cron": "* * * * *", "prompt": "p", "skill": None, "enabled": True, "last_run": 0},
        ]))
        _update_last_run(agent_config, "nope", 123)
        tasks = json.loads(schedule_file.read_text())
        assert tasks[0]["last_run"] == 0  # unchanged


class TestCheckAndFire:
    async def test_fires_due_task(self, schedule_file, agent_config):
        # Task with last_run=0 (never run) and cron="* * * * *" (every minute) → should fire
        schedule_file.write_text(json.dumps([
            {"id": "t1", "cron": "* * * * *", "prompt": "do it", "skill": None, "enabled": True, "last_run": 0},
        ]))
        mock_agent = AsyncMock()
        mock_agent.config = agent_config

        fired = await _check_and_fire(mock_agent)
        # Let the create_task coroutine run
        await asyncio.sleep(0)

        assert "t1" in fired
        mock_agent.handle.assert_called_once()
        call_kwargs = mock_agent.handle.call_args
        assert call_kwargs.kwargs["system_task_prompt"] == "do it"
        assert call_kwargs.kwargs["session_id"].startswith("sched:t1:")

    async def test_skips_disabled_task(self, schedule_file, agent_config):
        schedule_file.write_text(json.dumps([
            {"id": "t1", "cron": "* * * * *", "prompt": "do it", "skill": None, "enabled": False, "last_run": 0},
        ]))
        mock_agent = AsyncMock()
        mock_agent.config = agent_config

        fired = await _check_and_fire(mock_agent)

        assert fired == []
        mock_agent.handle.assert_not_called()

    async def test_skips_recently_run_task(self, schedule_file, agent_config):
        now = int(time.time())
        schedule_file.write_text(json.dumps([
            {"id": "t1", "cron": "* * * * *", "prompt": "do it", "skill": None, "enabled": True, "last_run": now},
        ]))
        mock_agent = AsyncMock()
        mock_agent.config = agent_config

        fired = await _check_and_fire(mock_agent)

        assert fired == []
        mock_agent.handle.assert_not_called()

    async def test_handles_malformed_json(self, schedule_file, agent_config):
        schedule_file.write_text("not json{{{")
        mock_agent = AsyncMock()
        mock_agent.config = agent_config

        fired = await _check_and_fire(mock_agent)

        assert fired == []

    async def test_handles_missing_file(self, tmp_path, agent_config):
        agent_config.context_dir = tmp_path
        # No schedules.json exists
        mock_agent = AsyncMock()
        mock_agent.config = agent_config

        fired = await _check_and_fire(mock_agent)

        assert fired == []

    async def test_loads_skill_into_prompt(self, schedule_file, agent_config, tmp_skills):
        skill_dir = tmp_skills / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: test\n---\nDo special things.")

        schedule_file.write_text(json.dumps([
            {"id": "t1", "cron": "* * * * *", "prompt": "do it", "skill": "my-skill", "enabled": True, "last_run": 0},
        ]))
        mock_agent = AsyncMock()
        mock_agent.config = agent_config

        fired = await _check_and_fire(mock_agent)
        await asyncio.sleep(0)

        call_kwargs = mock_agent.handle.call_args
        prompt = call_kwargs.kwargs["system_task_prompt"]
        assert "Do special things." in prompt
        assert "do it" in prompt

    async def test_fires_multiple_tasks_concurrently(self, schedule_file, agent_config):
        schedule_file.write_text(json.dumps([
            {"id": "t1", "cron": "* * * * *", "prompt": "p1", "skill": None, "enabled": True, "last_run": 0},
            {"id": "t2", "cron": "* * * * *", "prompt": "p2", "skill": None, "enabled": True, "last_run": 0},
        ]))
        mock_agent = AsyncMock()
        mock_agent.config = agent_config

        fired = await _check_and_fire(mock_agent)
        await asyncio.sleep(0)

        assert "t1" in fired
        assert "t2" in fired
        assert mock_agent.handle.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scheduler.py -v`
Expected: FAIL — `ImportError: cannot import name '_check_and_fire' from 'src.scheduler'`

- [ ] **Step 3: Implement the scheduler**

Create `src/scheduler.py`:

```python
# src/scheduler.py
"""Async scheduler that fires scheduled tasks via agent.handle()."""

import asyncio
import json
import logging
import os
import tempfile
import time

from croniter import croniter

from src.skills import load_skill

logger = logging.getLogger(__name__)


def _schedule_path(config):
    return config.context_dir / "schedules.json"


def _load_tasks(config) -> list[dict]:
    path = _schedule_path(config)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read schedules.json: %s", e)
        return []


def _update_last_run(config, task_id: str, timestamp: int) -> None:
    """Atomically update a task's last_run timestamp in the schedule file."""
    path = _schedule_path(config)
    try:
        tasks = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return
    for t in tasks:
        if t["id"] == task_id:
            t["last_run"] = timestamp
            break
    else:
        return  # task not found
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(tasks, f, indent=2)
        os.rename(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _is_due(task: dict, now: float) -> bool:
    """Check if a task is due: was there a fire time between last_run and now?"""
    last_run = task.get("last_run", 0)
    if last_run >= now:
        return False
    try:
        cron = croniter(task["cron"], last_run)
        next_fire = cron.get_next(float)
        return next_fire <= now
    except (ValueError, KeyError):
        return False


async def _run_task(agent, task_id: str, session_id: str, prompt: str) -> None:
    """Run a single scheduled task. Called via asyncio.create_task() for concurrency."""
    try:
        await agent.handle(
            message="",
            session_id=session_id,
            system_task_prompt=prompt,
        )
        logger.info("Scheduled task completed: %s", task_id)
    except Exception as e:
        logger.error("Scheduled task failed: %s — %s", task_id, e)


async def _check_and_fire(agent) -> list[str]:
    """Check all tasks and fire any that are due. Returns list of fired task IDs."""
    tasks = _load_tasks(agent.config)
    fired = []
    now = time.time()

    for task in tasks:
        if not task.get("enabled", True):
            continue
        if not _is_due(task, now):
            continue

        task_id = task["id"]
        prompt = task["prompt"]

        # Load skill content if specified
        if task.get("skill"):
            skill_content = load_skill(task["skill"], agent.config.skills_dir)
            if not skill_content.startswith("Skill not found"):
                prompt = skill_content + "\n\n" + prompt

        timestamp = int(now)
        session_id = f"sched:{task_id}:{timestamp}"

        # Update last_run before firing to prevent double-fires
        _update_last_run(agent.config, task_id, timestamp)

        logger.info("Firing scheduled task: %s (session %s)", task_id, session_id)
        asyncio.create_task(_run_task(agent, task_id, session_id, prompt))
        fired.append(task_id)

    return fired


async def run_scheduler(agent, interval_sec: int = 60):
    """Main scheduler loop. Runs forever, checking for due tasks every interval."""
    logger.info("Scheduler started (interval: %ds)", interval_sec)
    while True:
        await asyncio.sleep(interval_sec)
        try:
            fired = await _check_and_fire(agent)
            if fired:
                logger.info("Scheduler tick: fired %s", fired)
        except Exception as e:
            logger.error("Scheduler tick error: %s", e)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scheduler.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "feat: add async scheduler for system-initiated tasks"
```

---

### Task 6: Wire scheduler into run.py

**Files:**
- Modify: `run.py:14` (import) and `run.py:191-196` (TaskGroup)

- [ ] **Step 1: Add import**

Add to imports in `run.py` (after the other src imports, around line 14):

```python
from src.scheduler import run_scheduler
```

- [ ] **Step 2: Add scheduler to TaskGroup**

In `run.py`, add a new task to the `TaskGroup` block (after line 196):

```python
        tg.create_task(run_scheduler(agent))
```

- [ ] **Step 3: Add summarizer for schedule tool calls in _summarize_tool_call**

Add a case to the match statement in `_summarize_tool_call` (around line 53, before the `case _`):

```python
        case "schedule":
            action = args.get("action", "")
            task_id = args.get("id", "")
            return f"Schedule {action} {task_id}".strip()
```

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add run.py
git commit -m "feat: wire scheduler into runtime TaskGroup"
```

---

### Task 7: Update system prompt and create empty schedules.json

**Files:**
- Modify: `context/identity.md` (add scheduling section)
- Create: `context/schedules.json`

- [ ] **Step 1: Add scheduling section to identity.md**

Append to `context/identity.md`:

```markdown

## Scheduling

You can schedule tasks to run autonomously on a cron schedule using the `schedule` tool.
When a user asks you to do something regularly or at a specific time, use this tool to
set it up. Scheduled tasks run in their own session — you won't have conversation context,
so make the prompt self-contained. If the task needs a specific skill, set the skill field.
```

- [ ] **Step 2: Create empty schedules.json**

Create `context/schedules.json`:

```json
[]
```

- [ ] **Step 3: Run full test suite one final time**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add context/identity.md context/schedules.json
git commit -m "feat: add scheduling hint to system prompt and empty schedules file"
```
