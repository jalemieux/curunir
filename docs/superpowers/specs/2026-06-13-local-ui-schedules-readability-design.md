# Local UI — Schedules panel readability

**Date:** 2026-06-13
**Status:** Approved design, pending implementation
**Scope:** Frontend-only (`src/local_ui/static/`)

## Problem

The Local Web UI Schedules tab renders cron tasks as a flat table
(`id | cron | skill | prompt | state | next fire | last status | actions`).
Two readability problems:

1. **Prompt content is large** and sometimes **Markdown**, but it's dumped raw
   into a single table cell — unscannable, and it blows up row height.
2. **Cron is shown raw** (`0 7 * * 1-5`), which is hard to read at a glance.

## Goals

- Collapse each schedule so the large prompt stays out of the way until wanted.
- Render the prompt as Markdown when expanded.
- Show cron in human-readable English, without losing the raw expression.

Non-goals: any backend/API change, schedule-editing semantics change, portal
changes. The mock lives at `docs/schedules-mock.html` (reference only, not shipped).

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Layout | **Accordion cards** (replace the table) | A table cell is too cramped for multi-paragraph rendered Markdown; cards give it room and keep a scannable collapsed header. |
| Prompt overflow | **Fixed max-height (240px) + scroll** | Long prompts scroll inside the card instead of stretching it indefinitely. |
| Markdown | **Reuse the already-loaded global `marked.parse()`** | `index.html` already loads `marked@4` from CDN and the Memory panel already calls `marked.parse()`. Zero new dependency. |
| Human cron | **`cronstrue` (~10KB), vendored locally** | Cron→English has many edge cases; a proven lib beats hand-rolling. Vendored into `static/` so the loopback/Docker console works even if the CDN is unreachable. |

## Design

### Data — unchanged

`GET /api/schedules` already returns everything needed:
`id, cron, skill, prompt, enabled, next_fire (ISO), last_status`. No reader or
route change. This is a pure `src/local_ui/static/index.html` (+ one vendored
asset) change.

### Vendored asset

- Download `cronstrue` minified UMD bundle (global `window.cronstrue`) into
  `src/local_ui/static/cronstrue.min.js`.
- Reference it from `index.html` with `<script src="/static/cronstrue.min.js"></script>`
  alongside the existing `marked`/`hljs` tags. (`/static` is mounted from
  `src/local_ui/static/` in `local_web.py`.)

### Render — accordion cards

Replace `loadSchedules`'s table render and the `viewRow`/`editRow` helpers.

**Collapsed card header** (one scannable row, grid layout):

```
▶  <id> [skill-chip]   <human cron> (raw cron, grey)   ●enabled   next: <fmt>
```

- `<human cron>` = `cronstrue.toString(t.cron)`, wrapped in `try/catch`; on
  failure (or missing `cronstrue`) fall back to `<code>${raw cron}</code>`.
- Clicking the header toggles an `open` class on the card (pure CSS show/hide of
  the body — same interaction model the mock demonstrates).

**Expanded card body:**

```
last status: <last_status>   skill: <skill|—>
┌ prompt-render (max-height 240px, overflow:auto) ┐
│  marked.parse(t.prompt)                          │
└──────────────────────────────────────────────────┘
[Edit] [Enable/Disable] [Delete]
```

- Prompt rendered via `md(t.prompt)` (the existing `marked.parse` wrapper).
- Action buttons keep the existing `data-act` / `data-id` wiring and
  `onSchedAction` handler unchanged.

### Edit mode

`_schedEdit` stays the single source of truth for "which id is being edited."
When a card's id === `_schedEdit`, the card renders **expanded with the edit
form in its body** (the existing `#esched-cron` / `#esched-skill` /
`#esched-prompt` inputs + Save/Cancel), instead of the read-only render. The
existing `wireEditPreview()` (live next-runs preview) and the new-schedule
`<details>` form at the top are unchanged.

### Security / behavior preserved

- `esc()` still guards all interpolated attribute/text values; `marked.parse`
  is only applied to the prompt body, exactly as the Memory panel already does.
- No change to the token-gated mutating routes or their client calls.

## Testing

- Frontend-only, no Python logic added → no new unit tests required; existing
  `local_web` route tests stay green.
- Add a lightweight assertion that `/static/cronstrue.min.js` is served (the
  file exists under the mounted dir) if a cheap hook exists; otherwise manual.
- Manual verification: open the console Schedules tab, confirm (a) cards
  collapse/expand, (b) human cron shows with raw fallback on a bad expression,
  (c) a long Markdown prompt renders and scrolls, (d) edit/toggle/delete still
  work.

## Files touched

- `src/local_ui/static/index.html` — render + one `<script>` tag + CSS.
- `src/local_ui/static/cronstrue.min.js` — new vendored asset.
- Docs: update `docs/architecture.md` Local Web UI section + changelog;
  `README.md` if the Schedules tab is described there.
