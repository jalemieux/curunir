# Portal Skills Picker — Design

**Date:** 2026-05-15
**Status:** Approved design, ready for implementation planning

## Problem

The portal web UI exposes skills only through slash commands. A user has no
way to discover that, say, an `investment-memo` skill exists — they must
already know its name and type `/investment-memo`. The `/skills` command
prints a markdown table, but it is itself a slash command the user must know
to type, the table dumps every registered skill (including internal plumbing
skills), and the descriptions are agent-facing walls of trigger phrases.

Typing `/` is also awkward on a phone keyboard, so slash-only discovery is
poor on mobile — the portal's primary constraint.

## Goal

Give portal users a tappable, mobile-friendly way to discover and launch the
skills meant for them, each shown with a plain-English description of what it
does. No slash typing required.

## Validated Decisions

All five UX decisions below were chosen by the builder against side-by-side
mockups during brainstorming:

1. **Surface** — a ⚡ button in the composer row opens a picker. On mobile
   (≤640px) it is a bottom sheet that slides up; on desktop it is a centered
   popover. Same component, responsive.
2. **Tap behavior** — tapping a skill starts it immediately. No prefilled
   composer, no detail step.
3. **Opt-in metadata** — a skill appears in the picker only if its `SKILL.md`
   frontmatter declares a `portal_summary`. The data lives with the skill;
   there is no separate manifest.
4. **Layout** — a flat, searchable list (not category-grouped).
5. **Config zone** — a de-emphasized "Settings" footer block below the main
   skill list, holding identity/profile/preferences/help as smaller muted
   rows with no summaries.

## Scope

**In scope:** the ⚡ picker (bottom sheet / popover), the `skills_request` /
`skills_snapshot` WebSocket round-trip, the `portal_summary` / `portal_icon`
frontmatter fields, and adding those fields to the four launch skills.

**Out of scope:** the CLI (`cli.py` / `ws.py` channel) — it keeps the
existing `/skills` text command unchanged. No new skills are created; the
config-zone skills (`identity`, `profile`, `preferences`) already exist.

## Architecture

Three layers, each independently testable.

### Layer 1 — Skill metadata (`src/skills.py`)

Two new **optional** frontmatter fields, parsed by the existing
`parse_frontmatter` (naive `key: value`, quote-stripping — adequate for both):

- `portal_summary` — short, user-facing one-liner. Its **presence (non-empty)
  is the opt-in signal** for the picker. The agent-facing `description` is
  untouched and still used for the system-prompt manifest.
- `portal_icon` — a single emoji shown beside the skill. Optional; defaults
  to ⚡. Does not affect visibility.

The `Skill` dataclass gains `portal_summary: str | None = None` and
`portal_icon: str | None = None` (frozen dataclass — fields added with
defaults). `load_registry` populates them from frontmatter.

New helper `portal_skill_list(skill_dirs) -> list[dict]`:

- Calls `load_registry`, keeps only skills with a non-empty `portal_summary`.
- Returns, sorted by `name`:
  ```json
  {"name": "investment-memo",
   "display_name": "Investment memo",
   "summary": "Fact-checked investment memo on any stock, sector, or asset",
   "icon": "📊"}
  ```
- `display_name` is derived from `name`: hyphens → spaces, first word
  capitalized (`investment-memo` → "Investment memo").

`disabled: true` skills are already dropped by `load_registry`, so they can
never reach the picker even with a `portal_summary`.

### Layer 2 — Skills snapshot over WebSocket (`src/channels/portal.py`)

A new browser→container request frame, mirroring the existing
`history_request` / `history_snapshot` pattern exactly:

- **Request:** browser sends `{"command": "skills_request", "session_id":
  ...}` inside a `user_message` payload (or a top-level `{"type":
  "skills_request"}` frame, matching how history supports both forms).
- **Response:** container replies `{"type": "skills_snapshot", "session_id":
  ..., "skills": [...]}` where `skills` is the output of `portal_skill_list`.

`PortalChannel` gains a `skills_provider: callable[[], list[dict]] | None`
constructor arg (default `lambda: []`), set in `run.py` to
`lambda: portal_skill_list(agent.config.skill_dirs)`. A new
`_handle_skills_request` method sends the snapshot frame; `_read_loop` and
`_handle_user_message` route the new command to it, alongside the existing
`history_request` handling.

The skill list itself is global, but the *delivery* targets the one browser
that asked. So `skills_snapshot` carries the requester's `session_id` and the
portal service routes it **by `session_id`, exactly like `history_snapshot`**
— no new routing path. `portal/ws_agent.py` gets a `skills_snapshot` branch
parallel to its existing `history_snapshot` branch.

### Layer 3 — Frontend (`portal/static/index.html`)

**Trigger.** A ⚡ button added to the composer row next to 📎.

**Picker component.** A full-width overlay plus a panel:

- Mobile (`max-width: 640px`): panel anchored to the bottom, slides up, grab
  handle, max-height ~70vh.
- Desktop: panel centered as a popover.
- Same DOM; CSS media query switches the presentation.

**Contents, top to bottom:**

1. Title row ("⚡ Skills").
2. Search input — filters the main list client-side on `name` + `summary`,
   case-insensitive.
3. Main list — one flat, scrollable list. Each row: icon, display name,
   summary. Tapping a row launches the skill.
4. A faint "SETTINGS" divider, then the config footer — `identity`,
   `profile`, `preferences`, `help` as smaller, muted rows (icon + label, no
   summary). The search box does **not** filter the config footer.

**Config footer is a static frontend list.** Unlike the main list, the
config zone is fixed system chrome — four stable entries hardcoded in the
frontend as `{label, icon, command}`. This keeps `/help` (a built-in slash
command, not a skill) uniform with the three config skills, and means the
config skills need no frontmatter changes. The four commands
(`/identity`, `/profile`, `/preferences`, `/help`) all already resolve today
— three via the skill registry, `/help` via the intercepted handler.

**Data fetch.** On first open, the frontend sends `skills_request` and
renders the main list from the `skills_snapshot` reply. The result is cached
for the tab's lifetime; subsequent opens reuse it. While the first fetch is
in flight, the list area shows a brief loading state.

**Launching a skill.** Reuses the existing slash path with **zero new
invocation backend**: tapping any row (main or config) sends the existing
`{"command": "slash", "text": "/<name>", "session_id": ...}` frame — the same
frame a typed slash command produces. The composer echoes it locally exactly
as a typed slash does today, then the picker closes and the search box
clears.

**Dismissal.** Tapping the overlay, a ✕ button, or pressing Escape closes the
picker without launching anything.

## Data Flow

```
User taps ⚡
  → (first open) browser sends {command:"skills_request", session_id}
  → PortalChannel._handle_skills_request → portal_skill_list(skill_dirs)
  → {type:"skills_snapshot", session_id, skills:[...]} → portal
  → portal routes by session_id → requesting browser
  → picker renders main list + static config footer

User taps a skill row
  → browser sends {command:"slash", text:"/investment-memo"}
  → existing slash dispatcher: /investment-memo → "Use the
    `investment-memo` skill." enqueued
  → agent runs the skill, asks for any input it needs in chat
```

## Error Handling & Edge Cases

- **No opted-in skills** — `skills_snapshot` returns `[]`; the picker shows an
  empty-state message ("No skills available yet") and the config footer still
  renders.
- **`portal_summary` present but blank** — treated as not opted in (hidden).
- **Snapshot never arrives** (socket drop mid-fetch) — the picker shows a
  retry affordance; the cache is not populated, so the next open retries.
- **Unknown skill tapped** (skill removed between snapshot and tap) — the
  slash dispatcher already returns a polite "Unknown command" message; no
  special handling needed.
- **`portal_icon` missing or empty** — falls back to the default ⚡.

## Testing

- **`tests/test_skills.py`** — `portal_summary` / `portal_icon` parsing;
  `portal_skill_list` filtering (skills without `portal_summary` excluded,
  blank `portal_summary` excluded, `disabled` excluded), `display_name`
  derivation, sort order, icon default.
- **`tests/test_portal_channel.py`** — `skills_request` triggers a
  `skills_snapshot` frame carrying the provider's output; both the
  top-level-`type` and `command`-in-payload request forms.
- **`portal/tests/test_ws_agent.py`** — `skills_snapshot` routes to the
  browser by `session_id`, parallel to the existing `history_snapshot` test.
- **Frontend** — the portal has no JS test harness; the picker is verified
  manually (mobile bottom sheet, desktop popover, search filter, tap-to-launch
  on both main and config rows, dismissal).

## Rollout — Files Changed

**New frontmatter** (`portal_summary` + `portal_icon`) on the four launch
skills only:

| Skill | icon | summary (draft) |
|-------|------|-----------------|
| investment-memo | 📊 | Fact-checked investment memo on any stock, sector, or asset |
| financial-analysis | 💹 | Valuation, scenarios, and peer comparison for a public company |
| deep-research | 🔬 | Research any topic into a sourced written report |
| fact-checker | ✅ | Independently verify the claims in a report or article |

Everything else (internal plumbing skills, the onboarding sub-skills) gets no
frontmatter change and stays hidden. Adding a skill to the picker later is a
one-line frontmatter edit — no portal code touched.

**Code:**

- `src/skills.py` — `Skill` fields, `load_registry` population,
  `portal_skill_list` helper.
- `src/channels/portal.py` — `skills_provider` arg, `_handle_skills_request`,
  routing in `_read_loop` / `_handle_user_message`.
- `run.py` — wire `skills_provider` into the `PortalChannel(...)` constructor.
- `portal/ws_agent.py` / `portal/ws_browser.py` — route `skills_snapshot` to
  the requesting browser.
- `portal/static/index.html` — ⚡ button, picker component (CSS + JS), config
  footer, `skills_request` fetch + cache.

**Unchanged:** `src/slash_commands.py` (`/skills` text command stays for the
CLI); `cli.py` / `src/channels/ws.py`.
