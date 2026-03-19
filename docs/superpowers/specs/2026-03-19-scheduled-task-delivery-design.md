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
    "address": "jalemieux@gmail.com",
    "subject": "Morning Market Brief"
  }
}
```

- `delivery` — optional object. If omitted or null, no system delivery occurs (skill-handles-it pattern).
  - `channel` — string, required. The channel type to route through (e.g., `"email"`).
  - `address` — string, required. Channel-specific destination. Semantics are channel-specific (email address for email, could be a channel ID for future integrations).
  - `subject` — string, optional. Used as the email subject line. Defaults to `"Curunir: {task_id}"` if omitted.

### Schedule Tool Changes

- The `add` and `update` actions accept the optional `delivery` parameter.
- Validation: `channel` must be a known channel type. Use a hardcoded allowlist (e.g., `{"email"}`) since the schedule tool runs at conversation time and doesn't have access to the runtime channel registry.
- The `list` action displays the delivery config when present (channel and address).
- The tool's JSON schema (used by the LLM to construct calls) must include the `delivery` object and its sub-fields.

### Scheduler Routing

After `_run_task()` receives the result from `agent.handle()`, it checks for a `delivery` config on the task. If present and the result is non-empty, it constructs an `OutgoingMessage` and puts it on `out_queue`:

```python
result = await agent.handle(
    message="",
    session_id=session_id,
    system_task_prompt=prompt
)

delivery = task.get("delivery")
if delivery and result and not result.startswith(("Error:", "Sorry,")):
    subject = delivery.get("subject", f"Curunir: {task_id}")
    msg = OutgoingMessage(
        content=result,
        channel=delivery["channel"],
        session_id=session_id,
        reply_address={
            "to": delivery["address"],
            "subject": subject,
            "in_reply_to": None,
        },
    )
    await out_queue.put(msg)
```

Error responses (starting with `"Error:"` or `"Sorry,"`) are suppressed from delivery and logged instead. The agent's normal result text is delivered as-is.

### Wiring Changes

- **`run.py`**: Pass `out_queue` to `run_scheduler()`.
- **`src/scheduler.py`**: `run_scheduler()` accepts `out_queue` and passes it to `_run_task()`. `_run_task()` receives both `out_queue` and the full task dict (including `delivery`), constructs and enqueues the `OutgoingMessage` when delivery is configured.
- **`src/channels/email.py`**: `EmailChannel.send()` currently calls `gog.send_reply()`, which requires `subject` and `in_reply_to`. For scheduled task delivery, `in_reply_to` will be `None` (no existing thread). `send()` must branch on the presence of `in_reply_to`: when present, call `gog.send_reply()` as today; when `None`, call `gog.send_reply()` with the `--reply-to-message-id` flag omitted (conditionally build the CLI args). Additionally, `send()` must skip the `gog.thread_modify()` labeling step when `in_reply_to` is `None`, since the `session_id` for scheduled deliveries (e.g., `"sched:morning-brief:1742400000"`) is not a Gmail thread ID and would cause a noisy error.
- **Router**: No changes. Already consumes `OutgoingMessage` from `out_queue` and routes by `channel` field.

### Channel Availability

If a task's delivery channel is not enabled at runtime (e.g., `delivery.channel` is `"email"` but `EMAIL_ENABLED` is false), the router will log a warning and discard the message. This is acceptable — the task still executes and the result is logged. No validation at schedule-creation time since channel availability can change between restarts.

## Out of Scope

- **Alert/active-channel delivery** — deferred; requires separate design for real-time push semantics and fallback strategies.
- **Delivery retry/confirmation** — if send fails, it fails. Errors appear in logs. No retry queue or delivery tracking.
- **New channel types** — works with existing channels only. Adding Slack, SMS, etc. is a separate effort.
- **Advanced formatting** — the agent's response text becomes the email body as-is. No templating beyond the configurable subject line.
