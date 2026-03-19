# System-Initiated Agentic Loops — Design Spec

## Problem

Curunir's agentic loop only runs in response to user messages arriving via a channel (WebSocket, email). Some use cases require the agent to act autonomously on a schedule — checking GitHub issues, sending morning briefs, running maintenance tasks — without a user message as the trigger.

## Design

### Overview

Three components:

1. **Schedule tool** — CRUD operations for the agent to manage scheduled tasks during conversation
2. **Async scheduler** — a background coroutine that checks for due tasks every minute
3. **System-task mode in the agent loop** — allows `agent.handle()` to run from a system prompt with no user message

### Schedule Tool

A single `schedule` tool with an `action` parameter supporting four operations:

- **`list`** — return all scheduled tasks
- **`add`** — create a new task (requires `id`, `cron`, `prompt`; optional `skill`, defaults enabled)
- **`update`** — modify a task by ID (any of: `cron`, `prompt`, `skill`, `enabled`)
- **`remove`** — delete a task by ID

Storage: `context/schedules.json` — a JSON array of task objects:

```json
[
  {
    "id": "morning-brief",
    "cron": "0 9 * * *",
    "prompt": "Check my GitHub notifications and summarize anything that needs attention.",
    "skill": null,
    "enabled": true,
    "last_run": 0
  }
]
```

- `id`: human-readable slug chosen by the agent or user
- `cron`: standard 5-field cron expression (minute, hour, day-of-month, month, day-of-week)
- `prompt`: the instruction the agent receives when the task fires — must be self-contained (no conversation context available)
- `skill`: optional skill name to load before executing the prompt
- `enabled`: boolean toggle, defaults to `true`
- `last_run`: unix timestamp of the last execution, `0` if never run. Updated by the scheduler on fire. Used to prevent double-fires and detect missed runs after restart.

The tool is registered as a default tool (always available, not opt-in).

**File safety**: writes use atomic temp-file-then-rename to prevent partial reads by the scheduler running concurrently on the event loop thread (the schedule tool runs in `asyncio.to_thread()`).

### Async Scheduler

A background coroutine added to `run.py`'s `TaskGroup`:

- Wakes every 60 seconds
- Reads `context/schedules.json` fresh each tick (picks up changes from the schedule tool without restart)
- For each enabled task, uses `croniter` to check if a firing was **due since the last check** (not exact-minute matching — this avoids missed fires from `asyncio.sleep` drift)
- **Concurrency guard via `last_run`**: each task's `last_run` unix timestamp is stored in `schedules.json`. Before firing, the scheduler checks whether a firing was due since `last_run` using `croniter`. If `last_run` already covers the current fire window, skip it. On fire, `last_run` is updated atomically in the schedule file **before** execution starts — this prevents double-fires even if the task takes longer than 60 seconds or the process restarts mid-execution.
- This also enables **missed-fire detection**: on startup, the scheduler can compare `last_run` against `croniter.get_prev()` and catch up on tasks that should have run while the container was down.
- Spawns each task via `asyncio.create_task()` — non-blocking
- **Error handling**: wraps the JSON parse in try/except. On parse failure, logs a warning and skips the tick. Since the scheduler runs inside a `TaskGroup`, an unhandled exception would tear down the entire application.
- Logs each task fire and completion

### Agent Loop Changes

`agent.handle()` gains an optional parameter: `system_task_prompt: str | None = None`

When `system_task_prompt` is provided:

- The `message` parameter is ignored — no user message is added to conversation history
- The task prompt is appended to the system prompt as a clearly delimited section:

```
{static_prompt}

Current time: {iso_timestamp}

## Scheduled Task
{system_task_prompt}
```

- The LLM receives only the system prompt (with task instructions) and begins acting — no user turn
- The agent runs its normal tool-call loop until it produces a final text response
- The return value is logged but not routed to any channel
- The skill instructions within the prompt tell the agent where to put results (GitHub comments, email, files, etc.)
- **Session cleanup**: after the task completes, the session is removed from `agent.sessions` to prevent unbounded memory growth. Each execution is stateless — no history carries over between runs of the same scheduled task.
- **`_trim_history` compatibility**: since system-task sessions start with an assistant turn (no user message), the trim function's `while history[0]["role"] != "user"` guard must be updated to handle conversations with no user messages. Guard on `system_task_prompt` to skip the user-message-first assumption.

**Skill loading**: if the task has a `skill` field, the scheduler loads the skill content (via `src/skills.py`) and prepends it to the `system_task_prompt` before calling `agent.handle()`. The agent does not need to call `load_skill` itself — the skill instructions are already in the system prompt.

The scheduler calls:

```python
await agent.handle(
    session_id=f"sched:morning-brief:{int(time.time())}",
    system_task_prompt="Check my GitHub notifications and summarize anything that needs attention.",
)
```

### System Prompt Changes

Add to `context/identity.md`:

```markdown
## Scheduling

You can schedule tasks to run autonomously on a cron schedule using the `schedule` tool.
When a user asks you to do something regularly or at a specific time, use this tool to
set it up. Scheduled tasks run in their own session — you won't have conversation context,
so make the prompt self-contained. If the task needs a specific skill, set the skill field.
```

## Files Changed

| File | Change |
|---|---|
| `src/tools/schedule_tool.py` | New — CRUD operations on `context/schedules.json` with atomic writes |
| `src/tools/schemas.py` | Register the `schedule` tool schema as a default tool |
| `src/tools/dispatcher.py` | Wire `schedule` tool to the dispatcher |
| `src/scheduler.py` | New — 60-second async loop, uses `last_run` in schedule file for concurrency guard and missed-fire detection |
| `src/agent/agent.py` | Add `system_task_prompt` parameter to `handle()`, skip user message append, clean up session after completion, fix `_trim_history` for no-user-message sessions |
| `run.py` | Add scheduler coroutine to the `TaskGroup` |
| `context/identity.md` | Add scheduling paragraph |
| `context/schedules.json` | New — empty initial file (`[]`) |
| `requirements.txt` | Add `croniter` |

## What's Not In Scope

- No changes to channels, queues, or router
- No Docker or container changes
- No cron daemon — pure Python async scheduling
- No UI for managing schedules (the agent is the UI)
- No execution log or notification on failure (follow-up concern)

## Addendum: Known Gaps Discovered Post-Implementation

### 1. Output Delivery

The design routes the agent's final text response to the log only. Unless the task prompt or loaded skill explicitly instructs the agent to communicate results via a tool (email, GitHub comment, file write), the output is lost. This is a fundamental gap for informational tasks (e.g., morning briefings) where the entire point is for a human to read the result. Addressed in a follow-up spec: `2026-03-19-scheduled-task-delivery-design.md`.

### 2. Provider Compatibility — System-Only Messages

The original design placed the task prompt in the system prompt with no user turn. Some LLM providers (e.g., Google's GLM family) do not support conversations with only a system message and no user message — they require at least one user turn to produce a response. This was discovered during implementation and resolved by sending the scheduled task prompt as a user message (`{"role": "user", "content": "## Scheduled Task\n{prompt}"}`) rather than embedding it in the system prompt alone. This is a pragmatic workaround that trades prompt purity for broad provider compatibility.
