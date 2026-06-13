# Schedule "Run now" button — design

**Date:** 2026-06-13
**Status:** Approved (pending spec review)

## Problem

The local UI Schedules tab can create, edit, toggle, and delete cron tasks, but
there is no way to *run* one on demand. Operators have to wait for the next cron
slot to see whether a schedule actually works — painful when verifying a newly
created digest, or confirming a fix (e.g. that scheduled digests now email
correctly after the `email-send` allowlist fix). We want a per-schedule
**"Run now"** button that fires the task *exactly as the scheduler would*.

## Goals

- Fire a single scheduled task through the same path the scheduler uses: load
  the named skill (if any) and prepend it, run `agent.handle()` in system-task
  mode under a `sched:<id>:<ts>` session.
- Trigger it from the Schedules tab with one click, token-gated like the other
  mutating routes.

## Non-goals (YAGNI)

- No in-UI streaming of the run's output. The run is fire-and-forget; output
  goes wherever the task sends it (email, etc.), same as a real scheduler fire.
- No scheduling/queuing changes. This does not alter cron evaluation.

## Key decisions

1. **Test-fire semantics — do NOT stamp run metadata.** A manual fire runs the
   task but leaves `last_run` / `last_attempt_at` untouched, so the task's cron
   cadence and next-due calculation are unaffected. Rationale: the button is for
   "run now to verify it works" without disturbing the real scheduled run. (The
   `_is_due` baseline is `max(last_run, last_attempt_at)`; stamping either would
   suppress or shift the next genuine fire.)

2. **Fire-and-forget.** Tasks can run 10+ minutes. The route kicks off the run
   in the background and returns `202 {"status": "started", "id": ...}`
   immediately; the UI shows a "Fired `<id>`" toast. Blocking the HTTP request
   for minutes risks timeouts and is not how the scheduler behaves.

3. **Run even if disabled.** Manual fire bypasses the `enabled` check — you want
   to test a schedule before turning it on.

## Architecture

A single reusable **firing node** that both the scheduler loop and the new UI
route call, so "how to fire a task" lives in one place and cannot drift (the
same principle behind the `schedule_store` engine).

```
                    ┌──────────────────────────────────────┐
                    │  scheduler.fire_task(                 │   ← reusable node
                    │     agent, config, task,              │
                    │     *, record_run=True,               │
                    │     session_id=None)                  │
                    │  • load + prepend skill (allowlist)   │
                    │  • agent.handle(system_task_prompt=…) │
                    │  • session sched:<id>:<ts>            │
                    │  • mark_run ONLY if record_run        │
                    │  • returns response text              │
                    └──────────────────────────────────────┘
                        ▲                            ▲
        record_run=True │                            │ record_run=False
        (loop also does │                            │ (no metadata —
         mark_attempt   │                            │  cadence untouched)
         before fire)   │                            │
            ┌───────────┴─────────┐       ┌──────────┴───────────────────┐
            │ scheduler loop      │       │ POST /api/schedules/{id}/run  │
            │ _check_and_fire     │       │ (token-gated, local UI)       │
            └─────────────────────┘       └───────────────────────────────┘
                                                     ▲
                                                     │ "Run now" button
                                                     │ → toast "Fired <id>"
                                              ┌──────┴───────┐
                                              │ Schedules tab │
                                              └───────────────┘
```

### Components

1. **`scheduler.fire_task(agent, config, task, *, record_run=True, session_id=None)`**
   Extracted from today's `_check_and_fire` (skill load + prepend) and
   `_run_task` (handle + mark_run). Behavior:
   - Build the effective prompt: if `task["skill"]` is set, load it through the
     persona allowlist and prepend (skip cleanly if "Skill not found").
   - `session_id` defaults to `sched:<task_id>:<int(time.time())>`.
   - `await agent.handle(message="", session_id=…, system_task_prompt=prompt)`.
   - On success: `mark_run(success)` **iff** `record_run`. On exception:
     `mark_run(error)` iff `record_run`, then log. Returns the response string.

   `_check_and_fire` keeps doing `mark_attempt` before dispatch and calls
   `fire_task(..., record_run=True)` via `asyncio.create_task`. Net behavior of
   the scheduler is unchanged.

2. **`POST /api/schedules/{task_id}/run`** in `local_web.py`
   Token-gated (existing `_rest_token` / `_token_ok`). Looks up the task via the
   schedule store; `404` if not found. Kicks off
   `task_runner(task)` as a background `asyncio.create_task` and returns
   `202 {"status": "started", "id": task_id}`. Does not await the run.

3. **Wiring (`run.py`)** — construct a narrow `task_runner` callback and pass it
   into `LocalWebChannel`, matching the existing DI style (`cancel_session`,
   `history_provider`):
   ```python
   task_runner=lambda task: scheduler.fire_task(agent, config, task, record_run=False)
   ```
   The channel receives a capability, not the whole agent.

4. **UI (`src/local_ui/static/index.html`)** — a "Run now" button per schedule
   row. On click: `POST /api/schedules/<id>/run?token=…`; on `202`, show a
   "Fired `<id>`" toast; on error, an error toast.

## Error handling

| Case | Behavior |
|------|----------|
| Unknown `task_id` | `404` |
| Missing/bad token | `401`/`403` via existing helper |
| `fire_task` raises mid-run | Logged (same as scheduler today); never reaches the client — fire-and-forget already returned `202` |
| Skill not found for task | Prepend skipped, task still runs (existing behavior) |

## Testing

- **`fire_task` (test_scheduler.py)** with a mock `agent.handle`:
  - `record_run=True` → `mark_run` stamped; response returned.
  - `record_run=False` → metadata untouched (assert `last_run`/`last_status`
    unchanged); response returned.
  - skill prepend: when `task["skill"]` set, the prompt passed to `handle`
    starts with the skill content.
  - exception path: `record_run=False` leaves metadata untouched even on raise.
- **Route (test_local_web_channel.py)**:
  - `202` + `{"status":"started"}` for a known id; background runner invoked.
  - `404` for unknown id.
  - token gate: missing token rejected.
- Regression: existing scheduler tests still pass (net scheduler behavior
  unchanged after the extraction).

## Files touched

- `src/scheduler.py` — extract `fire_task`; refactor `_check_and_fire`/`_run_task`.
- `src/channels/local_web.py` — new route + `task_runner` param.
- `run.py` — wire `task_runner`.
- `src/local_ui/static/index.html` — "Run now" button + handler.
- `tests/test_scheduler.py`, `tests/test_local_web_channel.py` — coverage.
- Docs: `docs/architecture.md` (scheduler + local UI sections), `README.md`.
