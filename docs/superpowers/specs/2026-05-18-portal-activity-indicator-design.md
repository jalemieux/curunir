# Portal activity indicator — design

**Date:** 2026-05-18
**Status:** Approved (design)
**Scope:** `portal/static/index.html` only — no server or protocol changes.

## Problem

While the agent runs tools mid-turn, the chat portal gives the average user no
signal that work is ongoing. Today the only "working" cue is `.typing` — three
bouncing dots rendered *inside* the assistant message `.body`. The first
streamed text chunk overwrites `body.innerHTML`, which deletes the dots. So a
tool run that happens *after* some text has streamed shows nothing at all: the
reply appears to have stalled.

An astute user expands the collapsible tool ticker and sees activity; the
average user does not. Separately, the bouncing-dots indicator reads as a
generic chat-app element and sits awkwardly against the portal's spare,
mono-accented aesthetic.

## Goal

A single in-message indicator that:

1. Survives text streaming — stays visible for the whole in-progress turn.
2. Names the current activity in plain language, so an average user reads
   *what* is happening, not just *that something is*.
3. Fits the portal's minimal, monospace-accented look.

Non-goals: header/global indicators, composer changes, server-side changes.

## Approach

Replace `.typing` with a **shimmer status line**: one monospace line, appended
*after* `.body` (so streaming never wipes it), consisting of a small breathing
accent dot plus a left-to-right shimmering label. The label is "Thinking…"
until the first tool call, then tracks the most recent tool.

This was chosen over a spinner+word (heavier, more generic motion), a
"live-ified" tool ticker (a user who ignores the ticker keeps ignoring it), and
a header beacon (too far from where the eye rests). It also reuses the
`tool_calls` data already arriving in `renderAgentChunk` — no new data needed.

## Data available

The `_summarize_tool_call` helper (`run.py`) already sends each tool call to the
client as a human-readable string in `m.tool_calls`, e.g. `"Read portal/app.py"`,
`"Grep pattern='route'"`, `"Bash ls -la"`, `"LoadSkill comfyui"`. The status
line derives its label from the most recent such string.

## Components (all in `portal/static/index.html`)

### 1. CSS

Add a `.status` block and remove the `.typing` block (and its
`@keyframes typing-bounce`):

- `.status` — flex row, `margin-top: 8px`, monospace 12px; mirrors the existing
  `.msg details.tools .ticker` styling for visual consistency.
- `.status-dot` — 7px accent circle, `a-breathe` keyframe (scale + opacity
  pulse, 1.6s).
- `.status-label` — gradient-clipped text shimmer: `background` is a
  `linear-gradient` from `--header-text` → `--fg` → `--header-text`,
  `background-clip: text`, animated via a `shimmer` keyframe (2.2s linear).
- Under `@media (prefers-reduced-motion: reduce)`, disable both animations
  (static dot, static `--header-text` label).

### 2. Label wording — gerund map

A small client-side lookup turns the leading verb of a `tool_calls` string into
a gerund so the line reads as a live action, matching the approved mockup:

```
Read → Reading   Write → Writing   Edit → Editing
Grep → Searching   Glob → Searching   Bash → Running
LoadSkill → Loading skill   WebFetch → Fetching   Delegate → Delegating
```

Transform: split the string on the first space; if the first word is in the
map, replace it and re-join; otherwise use the string unchanged. Append `"…"`.
The label is truncated with `text-overflow: ellipsis` (single line, no wrap).
Before any tool call, the label is the literal `"Thinking…"`.

### 3. `appendMessage(role, thinking)`

When `thinking` is true, append a `.status` element (dot + label, label =
`"Thinking…"`) as the **last child** of the message, instead of putting
`.typing` inside `.body`. `.body` starts empty.

### 4. `appendToolCalls(msgEl, calls)` and the tool ticker

The status line and the collapsed ticker would otherwise both name the current
tool, one line apart — visually redundant. Resolved by **one indicator at a
time**:

- While a turn is live, the ticker is hidden entirely
  (`.msg:has(.status) details.tools { display: none }`) — the status line is
  the sole live indicator. On `m.final` the status line is removed and the
  ticker appears.
- The collapsed ticker shows only the tool **count** + an expand caret — no
  last-tool name (the `.ticker-last` element and its update are dropped).
  Clicking expands the full `.tool-list` as before.

Ordering fix: the `details.tools` ticker is inserted **before** any `.status`
line so the status line stays the message's last child —
`msgEl.insertBefore(tools, statusEl)` when a status line is present, else
`appendChild`.

### 5. `renderAgentChunk(m)`

After the existing `appendToolCalls` call, if the in-progress message has a
`.status` line and `m.tool_calls` is non-empty, update its label to the
gerund-transformed last entry of `m.tool_calls`. On `m.final`, remove the
`.status` line (replaces the current `removeThinking` call).

### 6. `removeThinking(el)` → `removeStatus(el)`

Rename and retarget: remove the `.status` child instead of the `.typing` child.
Update both call sites (`renderAgentChunk` final branch, and the connection-loss
path near line 682).

### 7. History rendering

`renderHistoryEntry` calls `appendMessage("assistant")` with `thinking` false —
completed turns get no status line. No change needed beyond confirming the
default-false path adds nothing.

## Data flow

```
turn starts → appendMessage(..., true) → .status "Thinking…" (last child)
text streams → body.innerHTML rewritten → .status untouched (sibling)
tool call   → on_tool_call → m.tool_calls → .status label → "Reading portal/app.py…"
m.final     → removeStatus() → ticker remains, status line gone
```

## Error handling / edge cases

- **Turn ends without `final`** (connection drop): the existing connection-loss
  handler near line 682 already strips the indicator — it now calls
  `removeStatus`. No regression.
- **Empty `tool_calls`**: label stays at its previous value (or "Thinking…").
- **Long tool strings**: clipped by `text-overflow: ellipsis`; the line never
  wraps or widens the message.
- **Interrupt** (`stopping` class): unaffected — `m.final` still arrives and
  removes the status line.

## Testing

Manual, in the portal chat view:

1. Send a prompt that triggers tools after text — confirm the status line stays
   visible through streaming and updates per tool.
2. Send a prompt with no tools — confirm "Thinking…" shows, then clears on final.
3. Reload a past conversation — confirm no status line on completed turns.
4. Toggle light/dark — confirm shimmer/dot legible in both.
5. Enable OS "reduce motion" — confirm animations are disabled, line still readable.
6. Interrupt a turn — confirm the status line is removed.

No automated tests: this is presentation-only and the portal has no JS test
harness. Existing portal Python tests are unaffected.
