# Portal Scratch Conversation — Design

**Date:** 2026-05-24
**Status:** Approved, ready for implementation plan

## Problem

The portal sidebar lists every conversation the user has ever had. Quick
utility chats — "polish this message", "what's the difference between X
and Y", "translate this to plain English" — get persisted alongside
multi-day projects, pollute the conversation list, and trigger memory
extraction the user never wanted. There is no surface in the portal for
"throwaway, don't keep this" interactions.

## Goal

Add a single pinned **Scratch** slot at the top of the portal sidebar that
provides an ephemeral conversation surface:

- Doesn't persist to disk.
- Doesn't appear in the saved conversation list.
- Doesn't trigger memory extraction or get archived.
- Clears when the user switches to any other conversation.
- Has a visible **Start over** action to reset in place.

The pitch: type whatever, don't worry about it.

## Decisions

| Question | Decision |
|----------|----------|
| Session id | Fixed constant `"scratch"` — singleton slot |
| Persistence | None — lives only in `agent.sessions` in-memory |
| Memory extraction | Hard-skip when `session_id == "scratch"` |
| Archive | Never written |
| Sidebar list visibility | Excluded from `conversations_snapshot()` |
| Clear trigger | (a) switching to another conversation; (b) explicit "Start over" button; (c) browser reload |
| In-flight reload behavior | On portal connect, browser sends a `scratch_discard` for the scratch session so the server pops any stale in-memory state |
| Clearing on switch-away | Browser sends `scratch_discard` for `"scratch"` before binding to the new conversation |
| Visual treatment | Tinted indigo row, accent left-edge stripe, no header label above it; "Conversations" subhead acts as divider between Scratch and saved threads |
| Empty state | Scratch row goes muted-grey ("Empty · type to start"); chat shows a calm placeholder with example chips |
| Force-expire on inactivity | **No.** As long as the user doesn't switch away, scratch persists in-session |

## Scope

In scope:
- New constant `SCRATCH_SESSION_ID = "scratch"` (single source of truth).
- Backend: skip `conversation_store.save()`, skip extraction, exclude from
  snapshot, handle `scratch_discard` command.
- Frontend: pinned slot above the conversation list, ephemerality banner
  with embedded **Start over** button, switch-away discard wiring,
  empty-state placeholder, on-connect discard.
- Tests covering: session not saved to disk, scratch excluded from
  snapshot, extraction skipped, `scratch_discard` semantics.

Out of scope:
- Spotlight overlay (Option 1 from mockup).
- Quick Actions launcher (Option 3 from mockup).
- "Save as conversation" promotion (deferred — keep it simple).
- Email or WS-CLI channels — scratch is portal-only.
- Per-user scratch (single user, single browser tab assumption; the slot
  is one in-memory entry keyed on `"scratch"`).

## Architecture

### Constants

A new module `src/agent/scratch.py` exporting `SCRATCH_SESSION_ID = "scratch"`
and `is_scratch(session_id) -> bool`. Imported wherever scratch needs to be
distinguished. Single source of truth, no string literals scattered.

### Backend touchpoints

**`run.py:agent_worker` (line 442 area).** Before calling
`conversation_store.save()`, skip when `is_scratch(msg.session_id)`. The
session stays in `agent.sessions` (in-memory) so multi-turn works, but
nothing reaches disk.

**`run.py:agent_worker` (line 349 area — `clear`/`reset` branch).** Add
handling for a new command `scratch_discard`: pop `agent.sessions[SCRATCH]`
and return. Do *not* call `extract_learnings`, do *not* call
`conversation_store.delete` (no file to delete). Empty content acknowledgement
goes back to the channel.

**`src/channels/portal.py:_handle_user_message`.** Accept
`command == "scratch_discard"` and forward as an `IncomingMessage` with
that command (no content). The agent_worker handles it.

**`src/agent/agent.py:conversations_snapshot` (line 244 area).** Filter
out `is_scratch(c["session_id"])` from the returned list, same way email
is filtered today.

**`src/memory_extractor.py`.** No change needed — extraction is driven by
`conversation_store.due_for_extraction()` which only sees on-disk files,
and we never write a file. Add a defensive early-return in
`extract_learnings()` if a caller ever passes scratch history directly,
gated on a session-id arg the function doesn't currently take. **Decision:**
defer; the architectural guarantee (no file → no extraction) is enough.

### Frontend touchpoints

All in `portal/static/index.html`.

**Sidebar markup.** A new `#scratch-slot` div above `#conversation-list`.
Sibling, not parent. Sidebar layout becomes:

```
#sidebar
├── #scratch-slot          (new — single fixed row)
├── .sidebar-divider       (the existing "Conversations" head, restyled with top border)
└── #conversation-list     (unchanged)
```

**Scratch row state.** Two visual states driven by whether the scratch
session has any messages in the current page session:

- *Active* — indigo tint, accent stripe, "{n} messages · clears on switch away"
- *Empty* — neutral grey, muted stripe, "Empty · type to start"

State is local to the page (no server signal). Set when:
- User sends a message to scratch → active.
- `scratch_discard` is sent → empty.
- A history snapshot for scratch returns empty → empty.

**Ephemerality banner.** When the scratch session is the active session,
render a banner strip below the chat header (above `#messages`):

```
Nothing here is saved. Switching to another conversation clears it.   [Start over]
```

Hidden when any other conversation is active. The **Start over** button:
sends `scratch_discard`, clears `#messages`, sets state to empty.

**Switch-away wiring.** In `switchConversation(id)`, before mutating
`sessionId`, check if the *current* session is scratch. If so, send
`scratch_discard` over the WS, then continue switching.

**On-connect discard.** When the WebSocket opens, send `scratch_discard`
unconditionally so any stale in-memory scratch on the server is cleared.
Cheap and idempotent.

**Sidebar click on the scratch slot.** Calls
`switchConversation(SCRATCH_SESSION_ID)` — same code path; the switch-away
check above naturally handles "switching to scratch from another
conversation" without sending a discard (the *prior* session wasn't
scratch).

**Empty-state inside scratch.** When `messages` is empty and scratch is
active, render the placeholder with the four example chips (mirrors the
mockup). Clicking a chip fills the composer (does not send), matching the
conversation-starters pattern.

## Data flow

**First scratch message:**
1. User clicks Scratch slot → `switchConversation("scratch")`.
2. Composer sends with `session_id: "scratch"`.
3. `agent_worker` runs the turn; agent.handle reads/writes
   `agent.sessions["scratch"]` (in-memory only).
4. After the turn, `conversation_store.save()` is skipped (gated on
   `is_scratch`). Nothing written to disk.
5. The portal does **not** receive a `conversations_snapshot` update for
   scratch — it never appears in the list.

**Switch to a saved conversation:**
1. User clicks another sidebar row.
2. Browser sees current session is scratch → sends
   `{command: "scratch_discard", session_id: "scratch"}`.
3. Browser updates `sessionId` to the new id, requests its history.
4. Server pops `agent.sessions["scratch"]`, replies with empty
   acknowledgement.

**Start over:**
1. User clicks Start over.
2. Browser sends `scratch_discard`.
3. Browser clears `#messages`, transitions banner/empty-state, leaves
   `sessionId` as `"scratch"`.

**Page reload:**
1. Browser connects.
2. Browser sends `scratch_discard` on connect.
3. Whatever was in `agent.sessions["scratch"]` is dropped.

## Error handling

- A `scratch_discard` arriving when nothing is in `agent.sessions["scratch"]`
  is a no-op. Implement with `agent.sessions.pop(SCRATCH, None)`.
- A `conversations_snapshot` that accidentally includes scratch (e.g. if a
  past version wrote a `scratch.json` to disk) is filtered client-side as
  a belt-and-braces measure too: any row with `session_id === "scratch"`
  is removed before render. The server filter is the real defense.
- The fixed id `"scratch"` is rejected by `_safe_session_id` only if it
  contains `/`, `\`, `.`, or is empty — none apply. Safe.

## Testing

| Test | File | What it verifies |
|------|------|------------------|
| `test_scratch_session_not_saved` | `tests/test_conversation_store.py` | A turn on session_id="scratch" produces no file in `context/conversations/` |
| `test_scratch_excluded_from_snapshot` | `tests/test_agent.py` | `conversations_snapshot()` excludes scratch even when present in `agent.sessions` |
| `test_scratch_discard_pops_session` | `tests/test_agent.py` or new `tests/test_run.py` | `scratch_discard` removes `agent.sessions["scratch"]` and does not invoke extract_learnings |
| `test_scratch_discard_does_not_extract` | `tests/test_memory_extractor.py` | extract_learnings is not called when discarding a scratch session (mock and assert not_called) |
| `test_portal_forwards_scratch_discard` | `tests/test_channels.py` | Portal channel converts `scratch_discard` payload into an IncomingMessage with that command |
| `test_is_scratch` | `tests/test_scratch.py` (new) | `is_scratch("scratch")` is True; everything else False |

Frontend changes are not unit tested today; verified by manual smoke at
PR time (mockup matches reality, switch-away clears, Start over works,
reload starts empty).

## Open questions

None blocking. Possible follow-ups:

- "Save as conversation" promotion link — deliberately deferred. Add if
  users actually ask for it.
- Per-channel scratch (e.g., scratch in the WS-CLI) — deferred; portal is
  the primary surface.
- Multi-tab behavior — both tabs share the same `agent.sessions["scratch"]`
  on the server. Two tabs racing on scratch is a known nonissue (single
  user) but worth noting.
