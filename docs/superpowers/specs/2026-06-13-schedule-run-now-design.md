# Schedules tab: "Run now" — design

**Date:** 2026-06-13
**Issue:** #374
**PR:** #375

## Problem

The local UI Schedules tab can create, edit, toggle, and delete cron tasks, but
there is no way to **run one on demand**. Operators must wait for the next cron
slot to confirm a newly created or fixed schedule actually works (e.g. that
scheduled digests now email correctly after the `email-send` allowlist fix). Add
a per-schedule **"Run now"** button that fires the task exactly as the scheduler
would.

## Approach

Extract the inline firing logic from the scheduler into **one reusable node** —
`scheduler.fire_task(agent, config, task, *, record_run, session_id)` — called by
**both** the scheduler loop and a new token-gated `POST /api/schedules/{id}/run`
route. A real fire and a test fire share the exact same path (skill-load +
prepend → `agent.handle()` in system-task mode under `sched:<id>:<ts>`), so they
cannot drift.

```
                         ┌─────────────────────────────┐
  scheduler loop ──────► │ fire_task(record_run=True)  │
  (cron due)             │   mark_attempt              │
                         │   build prompt (+skill)     │ ──► agent.handle()
  POST .../{id}/run ───► │   agent.handle()            │     (sched:<id>:<ts>)
  (record_run=False)     │   mark_run (success/error)  │
                         └─────────────────────────────┘
```

The two callers differ only in **policy**, expressed via `record_run`:

| caller          | `record_run` | `mark_attempt` | `mark_run` | enabled check | dispatch        |
| --------------- | ------------ | -------------- | ---------- | ------------- | --------------- |
| scheduler loop  | `True`       | yes            | yes        | yes (`_is_due`) | counts as the run |
| run-now (test)  | `False`      | no             | no         | **no**        | pure test-fire  |

Run-now is **fire-and-forget** (returns `202 {"status":"started"}`, runs via
`asyncio.create_task`) and runs **even if the task is disabled** — an operator
testing a freshly-fixed, still-disabled schedule is the primary use case.

`mark_attempt` moved *inside* `fire_task` (gated on `record_run`). Because it runs
synchronously before the first `await`, a second scheduler tick in the same loop
iteration still sees the task as no-longer-due, so there is no within-tick
double-fire (real ticks are ~60s apart regardless).

## Surfaces touched

- `src/scheduler.py` — new `fire_task` node + `_build_prompt` helper; `_check_and_fire`
  now calls `fire_task(record_run=True)`. Scheduler behavior unchanged.
- `src/channels/local_web.py` — `agent` added to `__init__`; new `POST
  /api/schedules/{id}/run` route (token-gated; 404 on unknown id; no enabled check).
- `run.py` — passes `agent=agent` into `LocalWebChannel`.
- `src/local_ui/static/index.html` — "Run now" button + `run` action + transient
  "Fired `<id>`" toast.

## Decisions / trade-offs

- **`agent` reference vs. callback closure.** Passed `agent` into `LocalWebChannel`
  (consistent with the chat bridge, which already drives the agent). A `run_now`
  callback wired in `run.py` was the alternative; trivially reversible.
- **No completion feedback.** Fire-and-forget means the UI confirms only that the
  run *started*; output lands wherever the task sends it (email, memory, etc.).
- **No debounce.** A double-click or a manual fire overlapping a scheduled fire
  each gets a distinct `sched:<id>:<ts>` session, so they don't collide, but a long
  task could run twice. Acceptable for an operator test action.
- **Trust boundary unchanged.** The route uses the same `context/.ws-token` gate as
  the other mutating schedule routes — same loopback boundary, widens no exposure.
