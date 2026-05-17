# Portal Conversation Starters — Design

**Date:** 2026-05-16
**Status:** Approved, ready for implementation plan

## Problem

The portal chat (`portal/static/index.html`) opens to an empty `#messages`
area on a fresh conversation. A new user is shown a blank screen and a
`What would you like to do?` placeholder, with no hint of what Curunir can
actually do. There is no on-ramp into a first conversation.

## Goal

Show example conversation starters on the empty chat screen so a user can
see — and act on — what the assistant is good at, in one click.

## Decisions

| Question | Decision |
|----------|----------|
| Layout | Centered greeting + arrow-prefixed text list (no emoji) |
| Click behavior | Fills the composer (does **not** send) |
| Content source | Derived from the portal skill list |
| Greeting | Static (`What would you like to do?`), no name/time logic |

## Scope

In scope: the empty-state UI in `portal/static/index.html`, plus eager
fetching of the skill list on connect.

Out of scope: new skill frontmatter, server-side changes, changes to the
skills picker, the skills-snapshot data shape, any non-portal channel.

## Architecture

All work lands in the single static file `portal/static/index.html`. No
server-side or schema changes.

### Component graph

```
ws.onopen ──┬──> history_request ──> history_snapshot ──┐
            └──> skills_request  ──> skills_snapshot ──> skillsCache
                                                          │
        messages empty? ──> renderEmptyState() <──────────┘
                                 │
                                 ├─ static greeting line
                                 └─ one starter row per cached skill
                                          │ click
                                          └─> fillComposer("/<name> ")
```

### Three new units

**1. `renderEmptyState()`**
Builds and inserts an `.empty-state` block into `#messages`:
- a static greeting line (`What would you like to do?`)
- one `.es-starter` row per skill in `skillsCache`, each rendered as
  `→ <portal_summary>`
- if `skillsCache` is null or empty, render the greeting alone (no list)

Idempotent: if an `.empty-state` block already exists it is replaced, so
the function can be called again when `skillsCache` arrives late.

What it depends on: the `skillsCache` global, the `messagesEl` global.

**2. `fillComposer(text)`**
Sets `inputEl.value = text`, focuses the textarea, moves the caret to the
end, and fires the existing autosize logic (the `input` event handler that
recomputes `inputEl.style.height`). Pure DOM helper, reused by every
starter row.

What it depends on: the `inputEl` global.

**3. Lifecycle wiring**
- On `history_snapshot` with **zero** messages → call `renderEmptyState()`
  after the (empty) render loop.
- In `startNew()` → after clearing `#messages`, call `renderEmptyState()`.
- In `appendMessage()` → remove any existing `.empty-state` block before
  appending, so the first real message (user or assistant) clears it.
- On `skills_snapshot` → after caching, if an `.empty-state` block is
  currently in the DOM, call `renderEmptyState()` to fill in the rows.

### Supporting change: eager skill fetch

Today `skills_request` is sent lazily, only when the skills picker opens
(`openSkills()`). The empty state needs the skill list on first paint, so
add a `skills_request` frame to `ws.onopen` alongside the existing
`history_request`. The existing `skills_snapshot` handler already caches
into `skillsCache`; the only addition is the empty-state refresh described
above.

Ordering is handled both ways: if `history_snapshot` (empty) arrives first,
`renderEmptyState()` renders the greeting with whatever `skillsCache` holds
(possibly nothing yet); when `skills_snapshot` arrives it refreshes the
rows. If `skills_snapshot` arrives first, it is cached and the later
`renderEmptyState()` call picks it up.

## Data flow

Each starter row corresponds to one entry from the portal skill list
(`portal_skill_list()` in `src/skills.py`), which already returns only
skills that opted in via a non-empty `portal_summary`. Today that is 4
skills: investment-memo, deep-research, financial-analysis, fact-checker.

- **Row text:** the skill's `portal_summary` (already a curated declarative
  phrase, e.g. "Research any topic into a sourced written report"). The
  `portal_icon` field is intentionally **not** used.
- **Click target:** `fillComposer("/" + skill.name + " ")` — the slash
  command plus a trailing space, caret left at the end.

The user then types the subject and sends. This routes through the
existing slash path in `send()` (`content.startsWith("/")`), which the
server-side dispatcher (`maybe_handle_slash` in `src/slash_commands.py`)
already handles, including `/<skill-name> <args>` — args are split off and
folded into the synthetic prompt.

## Rationale

- **Fill, not send:** every portal skill needs a subject ("research
  *what*?", "memo on *which* stock?"). Sending `/deep-research` with no
  argument would start a confused turn. Dropping `/deep-research ` into the
  composer with the caret ready hands the turn to the user at exactly the
  right point.
- **Skill-derived content:** the empty state stays in sync as skills opt in
  or out — no second list to maintain. The `portal_summary` strings are
  already written as user-facing declarative phrases, so they read well as
  starters with no new frontmatter field.
- **No emoji:** a plain `→`-prefixed list reads cleaner and matches the
  restrained visual style of the portal.

## Edge cases

- **No opted-in skills:** `renderEmptyState()` renders the greeting line
  only, no list. No empty `.es-list` container.
- **Skills arrive after first paint:** the `skills_snapshot` handler
  re-runs `renderEmptyState()` while the block is still showing.
- **Offline:** the empty state is static and renders regardless of agent
  status. The offline modal overlay already blocks interaction with
  `#messages` and the composer, so a starter cannot be clicked while
  offline — no special handling needed.
- **Tab reload mid-conversation:** `history_snapshot` carries the prior
  messages, count is non-zero, so the empty state is not rendered.

## Testing

The portal has no JavaScript test harness — `index.html` is a single
static file and is not exercised by the Python `pytest` suite. The
skills-snapshot data path (`portal_skill_list()`,
`_handle_skills_request()`) is already covered by existing Python tests and
is unchanged by this work.

Verification is manual, in a browser:
1. Open the portal on a fresh tab → empty state shows greeting + 4 starter
   rows.
2. Click a starter → composer fills with `/<skill> `, caret at end, nothing
   sent.
3. Type a subject, send → normal slash turn begins; empty state is gone.
4. Click **New** → empty state reappears.
5. Reload a tab with history → empty state does **not** appear; prior
   messages render.

## Files touched

- `portal/static/index.html` — empty-state markup styles, `renderEmptyState()`,
  `fillComposer()`, lifecycle hooks in `onServerMessage`/`startNew`/
  `appendMessage`, and the `skills_request` added to `ws.onopen`.
