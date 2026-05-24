# Portal Scratchpad — Design

**Date:** 2026-05-24
**Status:** Approved, modal pivot incorporated

## Problem

The portal sidebar lists every conversation the user has ever had. Quick
utility chats — "polish this message", "what's the difference between X
and Y", "translate this to plain English" — get persisted alongside
multi-day projects, pollute the conversation list, and trigger memory
extraction the user never wanted. There is no surface in the portal for
"throwaway, don't keep this" interactions.

## Goal

Add a **Scratchpad** modal that opens from a header button (next to "New
conversation"). The modal provides an ephemeral chat surface:

- Doesn't persist to disk.
- Doesn't appear in the conversation list.
- Doesn't trigger memory extraction or get archived.
- Clears when the modal is closed.
- Visually distinct from a persistent conversation so users don't form
  the wrong mental model.

The pitch: type whatever, don't worry about it.

## Why a modal (not a sidebar slot)

The first iteration of this design placed Scratch as a pinned slot at the
top of the sidebar. Inline testing showed the approach was confusing:
once you clicked into the slot, the chat surface looked identical to any
other conversation (same composer, same scroll area, same chrome). A
small banner above the chat trying to communicate "this is ephemeral"
got ignored — users formed their mental model from the surrounding
layout, not from text.

A modal is structurally different. It floats over the page, has its own
visual envelope, dismisses on Esc / backdrop click, and can't be confused
with the regular chat. The structural difference does the teaching that
copy cannot.

## Decisions

| Question | Decision |
|----------|----------|
| Surface | Modal overlay, centered, dimmed backdrop |
| Trigger | Header button labelled **Scratchpad**, next to **New conversation** |
| Session id | Fixed constant `"scratch"` — singleton |
| Persistence | None — lives only in `agent.sessions` in-memory |
| Memory extraction | Hard-skip when `session_id == "scratch"` |
| Archive | Never written |
| Sidebar list visibility | Excluded from `conversations_snapshot()` |
| Multi-turn | Yes — full chat inside the modal so follow-ups work |
| Tool calls | Hidden in the modal (kept lean) |
| Clear trigger | Closing the modal (Close button, Esc, or backdrop click); also on browser reload |
| Inactivity expiry | None — as long as the modal stays open, the session stays alive |
| In-flight reload behavior | On portal connect, browser sends `scratch_discard` so any stale in-memory state on the server is dropped |
| Backdrop dim | Yes, `rgba(10, 10, 25, 0.45)` |
| Companion rename | The header's **New** button is renamed to **New conversation** for clarity now that Scratchpad sits beside it |

## Scope

In scope:
- New constant `SCRATCH_SESSION_ID = "scratch"` shared between the agent
  module and the portal frontend.
- Backend: skip `conversation_store.save()` for scratch, handle a new
  `scratch_discard` command, exclude scratch from the sidebar snapshot.
- Frontend: Scratchpad modal markup, styles, JS, WS routing for inbound
  scratch frames, header button trigger.
- Tests covering: session not saved, scratch excluded from snapshot,
  extraction skipped, discard semantics, channel forwarding, dedup bypass.

Out of scope:
- "Save as conversation" promotion — deliberately deferred.
- Email or CLI channels — scratch is portal-only.
- Tool-call ticker inside the modal — Scratchpad is for quick interactions;
  if you need rich tool inspection, use a regular conversation.
- Cmd-K keyboard shortcut to open the modal — header button is enough for
  v1; can add later.
- Attachments inside Scratchpad — deferred.

## Architecture

### Constants

A new module `src/agent/scratch.py` exports `SCRATCH_SESSION_ID = "scratch"`
and `is_scratch(session_id) -> bool`. Imported wherever scratch needs to
be distinguished. Single source of truth, no string literals scattered.

The frontend mirrors the constant in `portal/static/index.html` as
`const SCRATCH_SESSION_ID = "scratch";` with a comment pointing to the
Python module.

### Backend touchpoints

**`run.py:agent_worker`.** Before calling `conversation_store.save()`,
skip when `is_scratch(msg.session_id)`. The session stays in
`agent.sessions` (in-memory) so multi-turn works, but nothing reaches
disk.

A new `scratch_discard` command branch pops the in-memory state without
invoking `extract_learnings`. A stray `clear`/`reset` arriving for the
scratch session routes through the same branch (belt-and-braces: never
write memory for scratch).

**`src/channels/portal.py:_handle_user_message`.** A `scratch_discard`
payload is forwarded as an `IncomingMessage` with that command,
*bypassing the dedup window* so two rapid Close+Open cycles both fire.

**`src/agent/agent.py:conversations_snapshot`.** Filter out
`is_scratch(c["session_id"])` from the returned list, same way email is
filtered today.

**`src/memory_extractor.py`.** No change. Extraction is driven by
`conversation_store.due_for_extraction()` which only sees on-disk files,
and we never write a file for scratch — the architectural guarantee is
enough.

### Frontend touchpoints (`portal/static/index.html`)

**Header.** Renames the "New" button to **New conversation**. Adds a new
**Scratchpad** button between Theme and New conversation. Both buttons
share the existing `.header-btn` style.

**Modal.** A new `#scratchpad-overlay` element at the bottom of `<body>`
(beside the existing `#offline-overlay` and `#skills-overlay`):

```
#scratchpad-overlay (fixed, dim backdrop, z-index 60)
└── #scratchpad-modal (centered card, max-width 640px, max-height 80vh)
    ├── #scratchpad-head        (title + sub + Close)
    ├── #scratchpad-body        (scrollable transcript; empty state by default)
    └── #scratchpad-composer    (textarea + Send button)
```

Distinct from `#sidebar`, `<main>`, and the main composer — completely
separate DOM subtree.

**State (page-local).** Three variables:
- `scratchpadOpen: boolean`
- `scratchInProgressMsg: HTMLElement | null` — the assistant bubble being
  streamed into
- `scratchTurnPending: boolean` — gates the Send button while a turn is
  in flight

**Inbound routing.** In `onServerMessage()`, frames with
`msg.session_id === SCRATCH_SESSION_ID` are routed to `renderScratchChunk()`
instead of `renderAgentChunk()`. Checked *before* the existing main-chat
session filter so scratch frames aren't dropped by the
"different session" guard. If the modal was closed mid-turn (a discard
was already sent), stragglers are ignored.

**Outbound.** `sendScratchMessage()` puts the user's text on the wire with
`session_id: "scratch"`. No attachments, no slash command processing
(simpler than the main `send()`).

**Open / close.**
- `openScratchpad()` shows the overlay and focuses the input. The empty
  state shown by default invites the four canonical use cases.
- `closeScratchpad()` sends `scratch_discard`, wipes the local transcript,
  and hides the overlay. Bound to: Close button, Esc, backdrop click.

**Esc precedence.** The existing global Esc handler triggers `interrupt()`
for in-flight main-chat turns. Scratchpad's Esc handling runs first when
the modal is open, so the user always has a one-key dismiss.

**On-connect discard.** When the WebSocket opens, the browser sends an
idempotent `scratch_discard` so any stale in-memory state on the server
is dropped before the user opens the modal.

**Session id reset.** On page load, if `localStorage["curunir-session-id"]`
contains `"scratch"` (from an earlier build of this feature), the browser
mints a fresh UUID. The main chat must never bind to the scratch id —
that would route all main-chat traffic into the modal.

## Data flow

**Opening Scratchpad:**
1. User clicks the **Scratchpad** header button.
2. Modal overlay shows; input is focused; empty state is visible.
3. No server traffic at this point — the slot only exists if the user
   actually sends a message.

**Sending a message:**
1. User types and presses Enter.
2. User bubble appears in the modal; placeholder assistant bubble
   appears with pulsing-dot "thinking" indicator.
3. Browser sends `{content, session_id: "scratch"}` over the WS.
4. `agent_worker` runs the turn; `agent.handle` reads/writes
   `agent.sessions["scratch"]` (in-memory only).
5. After the turn, `conversation_store.save()` is skipped because
   `is_scratch(session_id)` is true. Nothing reaches disk.
6. Streamed deltas come back tagged with `session_id: "scratch"`;
   `onServerMessage` routes them to `renderScratchChunk()`. Final frame
   removes the thinking state.

**Closing the modal:**
1. User clicks Close, presses Esc, or clicks the backdrop.
2. Browser sends `{command: "scratch_discard", session_id: "scratch"}`.
3. Server pops `agent.sessions["scratch"]` (no extraction, no archive).
4. Modal overlay hides; local transcript is wiped; empty state is
   restored for next open.

**Page reload:**
1. Browser connects to the portal.
2. After the standard handshake (history_request, skills_request,
   conversations_request), browser sends `scratch_discard`.
3. Any in-memory scratch on the server from a prior page session is
   dropped. Next time the user opens the modal, it's fresh.

## Error handling

- A `scratch_discard` arriving when nothing is in `agent.sessions["scratch"]`
  is a no-op (uses `dict.pop(key, None)`).
- A `conversations_snapshot` that accidentally includes scratch is
  filtered server-side by `is_scratch`. Defense in depth: the client
  also never renders a row with `session_id === SCRATCH_SESSION_ID`,
  but that path shouldn't fire.
- The Send button is disabled while a scratch turn is in flight, so the
  user can't queue duplicate sends. They can still close the modal
  mid-turn; stragglers are silently dropped by the
  `if (scratchpadOpen) renderScratchChunk(msg);` guard.
- Closing the modal while a turn is streaming: the server-side turn runs
  to completion (no interrupt is sent — Scratchpad doesn't expose stop),
  but the discard arrives and pops the session. Any straggler deltas are
  routed to a closed modal and dropped.

## Testing

| Test | File | What it verifies |
|------|------|------------------|
| `test_is_scratch` | `tests/test_scratch.py` | helper + constant |
| `test_scratch_excluded_from_snapshot` | `tests/test_agent.py` | sidebar list filter |
| `test_scratch_turn_is_not_persisted` | `tests/test_run_extraction.py` | no disk write |
| `test_scratch_discard_pops_session_without_extracting` | `tests/test_run_extraction.py` | discard pops, no extraction |
| `test_scratch_discard_with_no_session_is_noop` | `tests/test_run_extraction.py` | first-discard safe |
| `test_clear_command_on_scratch_does_not_extract` | `tests/test_run_extraction.py` | belt-and-braces |
| `test_scratch_discard_frame_enqueued_as_command` | `tests/test_portal_channel.py` | channel forwarding |
| `test_duplicate_scratch_discard_not_deduped` | `tests/test_portal_channel.py` | dedup bypass |

Frontend changes are not unit-tested today; verified by manual smoke at
PR time.

## Open questions

None blocking. Possible follow-ups:

- Cmd-K shortcut to open Scratchpad from anywhere.
- "Save as conversation" promotion link — if users actually ask for it.
- Tool-call ticker inside the modal — if quick chats start needing tools
  often.
- Attachment support inside Scratchpad.
