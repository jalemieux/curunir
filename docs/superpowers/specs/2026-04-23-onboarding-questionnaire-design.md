# Onboarding Questionnaire Design

**Issue:** [#26 — First-run onboarding: seed identity.md from a quick form](https://github.com/jalemieux/curunir/issues/26)
**Date:** 2026-04-23
**Scope:** Generate a useful `context/identity.md` from a small questionnaire the user fills out before first run. Form/web UI is deliberately out of scope (commercial onboarding deferred — see #26).

## Problem

A new user spinning up curunir today gets the maintainer's personal `identity.md`. The agent has no idea who the user is, how they want to be addressed, what they care about, or what's off-limits. Editing identity.md by hand is a poor first experience and most users won't do it.

## Solution

A markdown questionnaire (`onboarding/questions.md`) the user fills out before first launch. A small renderer reads the answers, fills a template, and writes the result to `context.default/identity.md`. The existing `bootstrap.py` (already in `onboarding/`) copies that to `context/identity.md` on first run.

Three artifacts, one pipeline:

```
onboarding/questions.md  →  onboarding/render.py  →  context.default/identity.md
                                                              │
                                                              │  (first run only)
                                                              ▼
                                                    context/identity.md
                                                    (via onboarding/bootstrap.py)
```

The user runs `python onboarding/render.py` once, then `python run.py`.

## Question set (8 total)

Captured in full in `onboarding/questions.md`. Summary:

| # | Question | Used for |
|---|---|---|
| 1 | Name / nickname | Greetings, references to the user |
| 2 | What you do (1–2 sentences) | "About you" section — anchors all future context |
| 3 | Top 2–3 things you want help with | Use cases section, drives proactive skill-loading |
| 4 | Timezone | "today/tomorrow" semantics, scheduling, send-times |
| 5 | Response style (terse / conversational / detailed) | Communication guidelines |
| 6 | Things curunir should never do without asking | Consent boundaries (rendered as "Always confirm before X" rules) |
| 7 | Personality (6 named flavors + custom) | Personality section |
| 8 | Open-ended catch-all | Additional context (only included if non-empty) |

## Generated identity.md structure

Sections, in order (most-personal first, boilerplate last — gets the LLM's attention budget on what matters):

1. **Personality** — from Q7
2. **About {name}** — from Q2 + Q4 timezone line + Q8 if present
3. **What {name} wants help with** — from Q3 (bulleted)
4. **Communication style** — from Q5 (expanded)
5. **Before you act** — from Q6 (bulleted consent rules)
6. **Memory** — boilerplate (universal)
7. **Scheduling** — boilerplate
8. **Creating skills** — boilerplate

## Design decisions

### 1. Persona expansions — inline in template (KISS)

The 6 named personas (Pragmatic peer, Executive assistant, Stoic butler, Friendly concierge, Witty companion, Chief of staff) each map to a 2–3 sentence expansion. Stored inline in `render.py` as a dict, not in separate files. Free-form persona text passes through verbatim. New personas = add a dict entry.

**Matching rule:** the user's Q7 answer is matched against the dict keys case-insensitively, ignoring whitespace and hyphens (so "Pragmatic peer", "pragmatic-peer", and "PRAGMATIC PEER" all hit). If no key matches, the answer is treated as freeform and passed through verbatim.

**Why:** Keeps the implementation in one file. No template engine, no file-discovery, no edge cases around missing expansions. The 6 expansions are short — total <500 words.

### 2. Output path — `context.default/identity.md`, then bootstrap copies

The renderer writes to `context.default/identity.md`. Existing `bootstrap.py` copies it to `context/identity.md` on first run only (never overwrites).

**Why:** Preserves the existing invariant that bootstrap is the only thing that writes to `context/`. If the user re-runs `render.py` after editing answers, they get an updated `context.default/identity.md` and can decide whether to wipe `context/identity.md` to re-bootstrap. No surprise overwrites of customizations.

### 3. Render is a separate step from bootstrap

`render.py` is invoked manually by the user, not chained into bootstrap. Bootstrap stays focused on its single job (copy seeds to context dir).

**Why:** Single responsibility. Render needs the user to have filled questions.md; bootstrap doesn't have a way to know whether they have. Failing in render shouldn't block the agent from starting (which depends on bootstrap). Two scripts, two clear responsibilities, two clear failure modes.

### 4. Answer parsing — `_Answer:_` markers in questions.md

`questions.md` uses `_Answer:_` placeholder markers below each question. The renderer scans for these and extracts the text on the following lines until the next `###` heading or `---` separator.

**Why:** The questionnaire is the source of truth and stays human-readable. No separate YAML/JSON config to keep in sync. The user edits one file. The format is simple enough to parse with ~20 lines of code.

### 5. Defaults & missing answers

| Question | If empty |
|---|---|
| Q1 (name) | Render uses "the user" throughout. Template prepends a one-line hint: *"You don't yet know the user's name — ask for it on first interaction and offer to update `context/identity.md`."* |
| Q2 (role) | Skip the "About" sentence; just include timezone line |
| Q3 (use cases) | Skip the "What you want help with" section entirely |
| Q4 (timezone) | Default to UTC; agent will ask if relevant |
| Q5 (style) | Default to "Conversational" |
| Q6 (boundaries) | Insert generic safe-default: "Always confirm before sending messages, spending money, or making irreversible changes." |
| Q7 (persona) | Default to "Pragmatic peer" |
| Q8 (open-ended) | Section omitted |

A user who fills nothing still gets a working, sensible identity.md.

## Out of scope (deferred to follow-on work)

- Web form / wizard UI (commercial onboarding — covered in #26 follow-up)
- Auto-detecting timezone from `/etc/timezone` or browser
- Examples gallery surfaced during onboarding (#28)
- Re-onboarding flow (editing answers after first run)
- Multi-user identities

## File layout after implementation

```
onboarding/
├── __init__.py
├── bootstrap.py          # already exists — copies context.default/ → context/
├── questions.md          # already exists — the questionnaire
├── render.py             # NEW — parses questions.md, fills template, writes output
└── identity_template.md  # NEW — template with {{placeholders}}

context.default/
└── identity.md           # NEW — generated by render.py, seeded by bootstrap.py

tests/
└── test_render.py        # NEW — covers parsing, defaults, all 6 persona expansions
```

## Acceptance criteria

- A user can fill `questions.md`, run `python onboarding/render.py`, run `python run.py`, and the agent addresses them by name with the persona they picked.
- A user who runs render with an empty questions.md gets a working identity.md with safe defaults.
- Re-running render after editing answers updates `context.default/identity.md` without touching `context/identity.md`.
- All 6 named personas render correctly. Custom freeform persona passes through.
- Existing bootstrap tests still pass; new render tests cover parsing, defaults, and persona expansion.
