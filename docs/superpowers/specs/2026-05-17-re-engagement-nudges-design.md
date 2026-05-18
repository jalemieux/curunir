# Re-engagement & Activation Nudges — Design

**Date:** 2026-05-17
**Issue:** #160 (supersedes the plan in PR #161)
**Status:** Design — awaiting review

## Problem

Curunir is purely reactive: it only acts when the owner messages it. A
freshly bootstrapped install can be forgotten before the owner ever builds
a habit, and an established owner who drifts away has no prompt to return.

We want the assistant to proactively email the owner when they go quiet —
but to do so as **infrastructure the owner cannot accidentally disarm**, and
with the **send path fully deterministic** so the LLM cannot forget to send,
double-send, or skip the gate.

## Principles

1. **Deterministic where it can be.** Whether/when to nudge, who to email,
   and the act of sending are plain Python. The LLM is invoked for exactly
   one thing: writing the email body.
2. **Not a user task.** The schedule is owned by code, not by
   `context/schedules.json`. The LLM `schedule` tool cannot see, edit, or
   disable it.
3. **Not naggy.** A capped series of nudges per quiet window, then silence
   until the owner returns.

## Two nudge series

The owner's situation determines both cadence and message intent. Series is
selected deterministically from account age at evaluation time.

| Series          | When                    | Quiet thresholds (days) | Cap | Message intent |
|-----------------|-------------------------|-------------------------|-----|----------------|
| **Activation**  | account age ≤ 14 days   | 2, 5, 10                | 3   | Teach: what the assistant can do, a concrete thing to try |
| **Re-engagement** | account age > 14 days | 7, 14, 21               | 3   | Continuity: where the owner left off, loose ends to pick up |

Both series are **inactivity-gated** — a new owner messaging daily receives
zero activation nudges; the series only sets a tighter quiet threshold.

The five numbers (window + two threshold lists) are env-configurable; the
table gives defaults.

## Architecture

```
agent_worker ──(genuine owner turn)──> record_interaction()
                                              │  resets nudge counter,
                                              ▼  sets last_interaction_at
                                     context/activity.json
                                              ▲
                       (read each daily tick) │
scheduler tick ──(code-registered system job)─┘
   ReengagementJob.run(agent):
     1. should_nudge(activity, now, config) ──> (GO|SKIP, series, reason)   [pure]
     2. on GO: gather series-specific memory context
     3. call_llm(context) ──> email body                                   [LLM]
     4. send_new(owner_email, subject, body)                                [deterministic]
     5. mark_nudge_sent()                                                   [deterministic]
```

### Components

**`src/reengagement.py`** *(new)* — deterministic core + the job.

- Activity store over `context/activity.json`:
  `{created_at, last_interaction_at, last_nudge_at, nudges_sent}`.
  - `load_activity(config)` and an atomic write helper (tempfile + `os.rename`,
    matching `scheduler.py`'s `_update_task_fields`).
  - `record_interaction(config)` — sets `last_interaction_at = now`; sets
    `created_at` if absent; **resets `nudges_sent = 0`** (re-arms the series).
  - `mark_nudge_sent(config)` — increments `nudges_sent`, sets
    `last_nudge_at = now`.
- `should_nudge(activity, now, config) -> (bool, series|None, reason)` — the
  pure predicate (logic below).
- `ReengagementJob` — a system job: `id`, `cron`, and `async run(agent)`
  implementing steps 1–5 above.

**`src/scheduler.py`** *(extended)* — reuse the existing tick loop for system
jobs.

- A `SystemJob` shape: `id: str`, `cron: str`, `run: Callable[[Agent], Awaitable]`.
- A module-level `SYSTEM_JOBS` registry, populated in code at startup. There
  is no file — nothing on disk for the `schedule` tool or the owner to edit.
- `_check_and_fire` additionally iterates `SYSTEM_JOBS`, applying the existing
  `croniter`-based `_is_due` check. System-job `last_run` is held **in memory**
  (a dict keyed by job id) — no `schedules.json` write, and no new state file.
  In-memory `last_run` resetting on restart is safe: the real idempotency
  guard is `nudges_sent` in `activity.json` (see below), so a duplicate tick
  cannot produce a duplicate email.

**Gmail client** *(extended)* — the client behind `EmailChannel` exposes only
`send_reply` / `send_with_attachments`, both requiring an `in_reply_to`. Add
`send_new(to, subject, body)` to start a fresh thread. The job calls this
directly — it does **not** go through the `email-send` skill.

**`run.py`** *(extended)*

- `agent_worker`: after the successful `agent.handle()` reply path, call
  `reengagement.record_interaction(agent.config)`. Control commands
  (`slash`, `clear`, `extract`) already `continue` before this path, so only
  genuine owner turns are recorded.
- At startup: register `ReengagementJob` into `SYSTEM_JOBS`. The existing
  `run_scheduler` task in the `TaskGroup` then fires it.

### `should_nudge` logic (pure)

```
if not email_enabled:                         -> (False, None, "email disabled")
if not REENGAGEMENT_ENABLED:                  -> (False, None, "feature disabled")
if not activity.last_interaction_at:          -> (False, None, "no baseline")

account_age_days = (now - activity.created_at) / 86400
quiet_days       = (now - activity.last_interaction_at) / 86400
series     = "activation" if account_age_days <= ACTIVATION_WINDOW_DAYS
             else "reengagement"
thresholds = ACTIVATION_THRESHOLDS if series == "activation"
             else REENGAGEMENT_THRESHOLDS

if activity.nudges_sent >= len(thresholds):   -> (False, series, "series exhausted")
next_threshold = thresholds[activity.nudges_sent]
if quiet_days < next_threshold:               -> (False, series, "quiet <Nd, need <Md>")
                                              -> (True, series, "<series> nudge K at Nd quiet")
```

`nudges_sent` is a single counter for the current quiet window, shared across
series. Consequences, accepted by design:

- A window's nudge times are `thresholds[nudges_sent]` — nudge 1 at the first
  threshold, nudge 2 at the second, etc.
- If the account crosses the 14-day boundary mid-window, the series flips but
  the counter carries over. An owner who exhausted the 3 activation nudges
  does **not** then receive re-engagement nudges in the same unbroken quiet
  window. Three nudges is enough; only an owner interaction re-arms.
- `record_interaction` resetting `nudges_sent` to 0 is the sole re-arm. An
  owner replying to a nudge email lands as a normal turn and re-arms naturally.

### Message composition (the one LLM step)

The job gathers **series-specific** context and calls `call_llm` once
(no tool loop, no `agent.handle()`):

- **Activation:** `context/identity.md` (who the assistant is) + a generic
  capability list. Little memory exists yet, so the email teaches.
- **Re-engagement:** `context/memory/summaries/timeline.md` (recent
  interactions, loose ends) + `profile.md` + `preferences.md`. The email
  references concrete unfinished threads.

Memory files may be missing on fresh installs — the job passes whatever
exists and the prompt instructs the model to degrade to generic suggestions
rather than fail.

The **subject** is templated per series (deterministic); the model writes
only the **body**. The email is warm, concise, non-intrusive, and suggests
concrete ways the assistant can help.

## Configuration (env vars)

| Var | Default | Purpose |
|-----|---------|---------|
| `REENGAGEMENT_ENABLED` | `false` | Master opt-in. Off by default — the feature sends email unprompted. |
| `REENGAGEMENT_CRON` | `0 9 * * *` | When the daily tick evaluates the gate. |
| `ACTIVATION_WINDOW_DAYS` | `14` | Account-age boundary between the two series. |
| `ACTIVATION_THRESHOLDS` | `2,5,10` | Quiet-day thresholds for the activation series. |
| `REENGAGEMENT_THRESHOLDS` | `7,14,21` | Quiet-day thresholds for the re-engagement series. |

Recipient is the owner mailbox (`GOOGLE_DELEGATED_USER`). With email disabled
or `REENGAGEMENT_ENABLED=false`, the gate returns SKIP and nothing is sent.

## Error handling

- **Email disabled / feature disabled / no baseline** — `should_nudge`
  returns SKIP with a reason; the job logs and returns. No partial state.
- **`call_llm` fails** — the job logs the error and returns **without**
  calling `mark_nudge_sent`, so the next daily tick retries the same nudge.
- **`send_new` fails** — same: no `mark_nudge_sent`, retried next tick.
  `mark_nudge_sent` runs only after a confirmed send.
- **Corrupt `activity.json`** — `load_activity` logs and treats it as empty
  (no baseline → SKIP), matching `scheduler.py`'s tolerance of a bad
  `schedules.json`.
- **Scheduler restart** — in-memory `last_run` resets; a same-day re-fire is
  harmless because `nudges_sent`/`quiet_days` gating prevents a duplicate send.

## Testing

- **`tests/test_reengagement.py`** *(new)* — pure logic against `should_nudge`
  / `select_series`:
  - Series selection at the 14-day boundary.
  - Activation thresholds: quiet `< 2d` → SKIP; `≥ 2d` → GO nudge 1; after
    `nudges_sent` advances, GO again only at 5d, then 10d, then exhausted.
  - Re-engagement thresholds: same shape at 7/14/21.
  - Anti-nag: `nudges_sent >= len(thresholds)` → SKIP; `record_interaction`
    resets the counter and re-arms.
  - Gates: `email_enabled=False`, `REENGAGEMENT_ENABLED=False`, and missing
    `last_interaction_at` each → SKIP.
  - Round-trip: `record_interaction` → `load_activity` → `mark_nudge_sent`
    against a `tmp_context` dir; atomic write leaves valid JSON.
- **`tests/test_scheduler.py`** *(extended)* — a registered `SystemJob` fires
  on its cron via the shared tick; `schedules.json` user tasks still fire;
  in-memory `last_run` prevents a same-tick double fire.
- **`tests/test_channels.py`** or `test_run_extraction.py` — `agent_worker`
  writes `activity.json` after a normal turn and **not** after a control
  command.
- **Job integration** — `ReengagementJob.run` with `call_llm` and `send_new`
  mocked: GO path calls `send_new` then `mark_nudge_sent`; a `send_new`
  failure leaves `nudges_sent` unchanged.
- `pytest tests/` stays green.
- **Manual:** set `REENGAGEMENT_ENABLED=true` and `ACTIVATION_THRESHOLDS=0,...`,
  confirm one email sends and the next daily tick does not re-send until a new
  owner message lands.

## Out of scope / accepted limitations

- **No per-channel owner check.** Recording in `agent_worker` covers all
  current owner channels (WS/CLI, email, portal). A future non-owner channel
  would need an owner check at the recording point.
- **Account age on pre-existing installs.** An install with history but no
  `activity.json` gets `created_at = now` on the first post-deploy turn, so it
  briefly looks "new" and receives activation-series nudges. Harmless; not
  worth back-dating from archives.
- **Cross-series counter carry-over** — described above; intentional.
