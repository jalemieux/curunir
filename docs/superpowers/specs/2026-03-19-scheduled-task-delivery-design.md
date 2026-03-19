# Scheduled Task Output Delivery

**Date:** 2026-03-19
**Status:** Draft

## Problem

When scheduled tasks execute via `agent.handle(system_task_prompt=...)`, the agent's final text response is logged and discarded. Unless the task's skill or prompt explicitly instructs the agent to use a tool (e.g., email, GitHub comment) to deliver results, the output is lost.

This is a fundamental gap for informational tasks like a morning briefing, where the entire point is for a human to read the result.

## Use Cases Analyzed

Three scheduled task patterns were identified:

1. **Route to channel** — e.g., morning market briefing delivered to email. The agent's final response is the deliverable.
2. **Skill handles it** — e.g., work on GitHub issues. The skill instructs the agent to post on the issue directly. No system-level delivery needed.
3. **Alert to active channel** — e.g., stock price alert pushed to whatever channel is connected. **Out of scope** — requires real-time delivery semantics (push notifications, fallback strategies) that warrant a separate design.

This spec addresses use case 1 only. Use case 2 already works. Use case 3 is deferred.

## Design

### Task Definition Schema

An optional `delivery` field is added to the schedule task schema:

```json
{
  "id": "morning-brief",
  "cron": "0 9 * * *",
  "prompt": "Summarize market conditions...",
  "skill": "market-brief",
  "enabled": true,
  "last_run": 0,
  "delivery": {
    "channel": "email",
    "address": "jalemieux@gmail.com"
  }
}
```

- `delivery` — optional object. If omitted or null, no system delivery occurs (skill-handles-it pattern).
  - `channel` — string, required. The channel type to route through (e.g., `"email"`).
  - `address` — string, required. Channel-specific destination (e.g., an email address).

The schedule tool's `add` and `update` actions accept the optional `delivery` parameter and validate that `channel` is a known channel type.

### Scheduler Routing

After `_run_task()` receives the result from `agent.handle()`, it checks for a `delivery` config on the task. If present and the result is non-empty, it constructs an `OutgoingMessage` and puts it on `out_queue`:

```python
result = await agent.handle(
    message="",
    session_id=session_id,
    system_task_prompt=prompt
)

delivery = task.get("delivery")
if delivery and result:
    msg = OutgoingMessage(
        content=result,
        channel=delivery["channel"],
        session_id=session_id,
        reply_address={"to": delivery["address"]},
    )
    await out_queue.put(msg)
```

### Wiring Changes

- **`run.py`**: Pass `out_queue` to `run_scheduler()`.
- **`src/scheduler.py`**: `run_scheduler()` accepts `out_queue` and passes it to `_run_task()`. `_run_task()` constructs and enqueues the `OutgoingMessage` when delivery is configured.
- **No changes to router or channels.** The router already consumes `OutgoingMessage` from `out_queue` and routes by the `channel` field. The email channel's `send()` already uses `reply_address["to"]` for the recipient.

## Out of Scope

- **Alert/active-channel delivery** — deferred; requires separate design for real-time push semantics and fallback strategies.
- **Delivery retry/confirmation** — if send fails, it fails. Errors appear in logs. No retry queue or delivery tracking.
- **New channel types** — works with existing channels only. Adding Slack, SMS, etc. is a separate effort.
- **Subject line / formatting control** — the agent's response text becomes the email body as-is. No templating or subject customization.
