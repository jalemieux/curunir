# Conversational Onboarding Design

**Status:** draft  •  **Date:** 2026-05-14  •  **Supersedes:** draft PR #105  •  **Closes:** issue #104 (Goal 1 only — memory export/import deferred)

## Problem

First-run setup is offline today: the user fills `onboarding/questions.md`, runs an LLM separately, copies the result into `context/identity.md`. There's no in-agent flow, no first-run detection, no way to re-tune one aspect later.

Draft PR #105 attempted to address this but predates PR #107 (the `## Personality` block restructuring) and proposed a single monolithic skill. This spec replaces it.

## Goals

1. **Conversational onboarding** that runs inside the agent on first launch — channel-agnostic.
2. **Three independently-callable sub-skills** nested under `skills/onboarding/`: `profile`, `preferences`, `personality`. Each is its own slash command after onboarding (`/profile`, `/preferences`, `/personality`).
3. **Hard gate**: a not-yet-onboarded user's first message kicks off onboarding directly — no "please run /onboard" reply. Scheduled tasks bypass the gate.
4. **Budget**: ≤6 user prompts end-to-end.
5. **Initializes three files** with real content: `context/memory/profile.md`, `context/memory/preferences.md`, `context/identity.md` (`## Personality` block).

## Architecture

### Skill layout — real nesting

Requires a 3-line change in `src/skills.py` (today's `glob("*/SKILL.md")` only matches one level deep):

```
skills/onboarding/
├── SKILL.md          ← orchestrator (slash: /onboarding)
├── profile/
│   └── SKILL.md      ← slash: /profile
├── preferences/
│   └── SKILL.md      ← slash: /preferences
└── personality/
    └── SKILL.md      ← slash: /personality
```

`load_registry()` switches from `glob("*/SKILL.md")` to `rglob("SKILL.md")`. Existing top-level skills are unaffected (they still match). One audit step at implementation time: confirm no current skill subdirectory contains a stray `SKILL.md` that would now get picked up.

### Conversation flow

```
User connects, sends any message (e.g., "hi")
   │
   ▼
agent.handle(incoming)
   │
   ▼
Gate check:
   .onboarded missing  AND
   history is empty    AND
   not incoming.system_task
   │ ALL TRUE
   ▼
Rewrite incoming.text →
   "The user has just connected and isn't onboarded yet.
    Open with a one-line preamble like 'Since you're new,
    let's get you set up — about a minute.' Then use the
    `onboarding` skill to walk them through it."
   │
   ▼
LLM call — invokes onboarding orchestrator via load_skill
   │
   ▼
Orchestrator: load_skill profile → conversation → write profile.md
              load_skill preferences → conversation → write preferences.md
              load_skill personality → conversation → write identity.md
              touch context/.onboarded
              confirm "All set."
   │
   ▼
Future turns: gate stays quiet (.onboarded exists OR history non-empty)
```

### Hard gate

Add at the top of `src/agent/agent.py::Agent.handle()`:

```python
onboarded = (self.config.context_dir / ".onboarded").exists()
history = self.histories.get(incoming.session_id, [])

if not onboarded and len(history) == 0 and not incoming.system_task:
    incoming.text = (
        "The user has just connected and isn't onboarded yet. "
        "Open with a one-line preamble like 'Since you're new, "
        "let's get you set up — about a minute.' Then use the "
        "`onboarding` skill to walk them through it."
    )
```

**Why these three conditions:**
- `.onboarded` missing → user hasn't completed the flow.
- `len(history) == 0` → this is the first turn in this session. Once the orchestrator is mid-conversation, `history` is non-empty so the gate stays quiet and user answers flow to the LLM unchanged.
- `not incoming.system_task` → scheduled tasks (ai-digest, introspection, cron) have empty per-task histories and would otherwise be hijacked.

**Edge case accepted:** container restart with onboarding incomplete → history empty again on next message → gate re-fires → onboarding restarts. Cost is small (≤6 prompts) and avoids a state-machine for `.onboarding_in_progress`.

**Verify at implementation time:** how scheduled tasks are flagged today. The plan task that adds the gate must confirm `incoming.system_task` (or equivalent) exists and is True for scheduler-originated messages. If not, the right flag is added there.

### Sub-skill responsibilities

| Skill | Prompts (2 each) | Writes |
|---|---|---|
| `profile` | 1. "What should I call you, and any preferred form (nickname, title)?"  2. "One line: what do you do, or what do you most want my help with?" | `context/memory/profile.md` — H2 sections `## Name`, `## Role / Focus` |
| `preferences` | 1. "How do you like to be communicated with — formal, casual, blunt, warm? Anything else?"  2. "Response length default — terse, balanced, or detailed?" | `context/memory/preferences.md` — H2 sections `## Communication style`, `## Response length` |
| `personality` | 1. "What should I call myself?"  2. "How do you picture me? One paragraph — used for an avatar and for me to talk about my own appearance." | `context/identity.md` — replaces `### Identity` block inside `## Personality`. Also drafts `### Voice` and `### Boundaries` *from the preferences answers* — no extra questions. `### Perspective`, `### Opinions`, `### Quirks` stay at the PR #107 seed defaults. |

Each sub-skill's `SKILL.md` is prose only (no code). The LLM reads it, asks the questions, parses the free-form answers, and writes the file using existing `edit`/`write` tools.

### Orchestrator (`skills/onboarding/SKILL.md`)

Prose. Instructs the agent:

1. If `context/.onboarded` exists, ask the user whether to redo everything or pick one section.
2. Otherwise: `load_skill profile`, follow it, write file.
3. `load_skill preferences`, follow it, write file.
4. `load_skill personality`, follow it. Use the preferences answers from step 3 to draft `### Voice` and `### Boundaries` in the same write.
5. Write empty `context/.onboarded` marker.
6. Confirm to user: "All set. You can re-run any section anytime with `/profile`, `/preferences`, or `/personality`."

### Slash command routing

PR #120 already routes any registered skill name to a skill-forcing slash command. The four skills land for free as `/onboarding`, `/profile`, `/preferences`, `/personality`. No new wiring in `src/slash_commands.py`.

## Files affected

| File | Change | Purpose |
|---|---|---|
| `src/skills.py` | edit (3 lines) | `glob("*/SKILL.md")` → `rglob("SKILL.md")`. |
| `src/agent/agent.py` | edit (~7 lines) | Hard gate at top of `Agent.handle()`. |
| `src/agent/types.py` (or wherever `IncomingMessage` lives) | check/edit | Ensure `system_task` flag (or equivalent) exists and is set True by scheduler-originated messages. Add if missing. |
| `skills/onboarding/SKILL.md` | create | Orchestrator. |
| `skills/onboarding/profile/SKILL.md` | create | 2-Q profile sub-skill. |
| `skills/onboarding/preferences/SKILL.md` | create | 2-Q preferences sub-skill. |
| `skills/onboarding/personality/SKILL.md` | create | 2-Q personality sub-skill. |
| `tests/test_skills.py` | edit | Add a test that nested `skills/onboarding/profile/SKILL.md` is discoverable; existing top-level discovery still works. |
| `tests/test_agent.py` | edit | Gate tests: fires on (no marker + empty history + not system_task); stays quiet when any condition fails. |
| `tests/test_onboarding_flow.py` | create | Mock-LLM integration: scripted 6-turn conversation; assert all three files written and `.onboarded` exists. |
| `CLAUDE.md` | edit | One-paragraph Onboarding section pointing at the new flow + slash commands. |

## Out of scope

- **Memory pack export/import** (was in draft PR #105 and issue #104 Goal 2). Deferred.
- **Avatar image generation.** The personality skill captures the visual *description* into `### Identity`. PNG creation stays manual per existing `onboarding/README.md`.
- **Implicit personality refinement** (issue #104 Goal 3). Already happens generally via the memory extractor; explicit feedback loops are a separate issue.
- **Migration helper** for users with existing hand-edited `context/`. They keep what they have.

## Test plan

**Unit**
- `tests/test_skills.py` — discovery picks up nested SKILL.md files; existing top-level pickup unbroken.
- `tests/test_agent.py` — gate fires only when all three conditions hold; rewrites `incoming.text` to the expected directive.

**Mock-LLM integration**
- `tests/test_onboarding_flow.py` — scripted LLM responses drive a 6-turn flow against a `tmp_context`. Assert: `profile.md` has the right H2 sections, `preferences.md` likewise, `identity.md` has the new `### Identity` content plus `### Voice` / `### Boundaries` derived from preferences, `.onboarded` exists.

**Manual end-to-end** (Docker)
1. `docker compose down -v && rm -rf context/ && docker compose up --build` → connect via CLI → say "hi" → expect preamble + first profile question.
2. Walk through all 6 prompts → verify three files populated, `.onboarded` present, subsequent message handled normally.
3. After completion: `/preferences` → re-asks just the two preferences questions; rewrites preferences.md; other files untouched.
4. Trigger ai-digest scheduled task before step 1 finishes → confirm it bypasses the gate (system_task path).

## Risks

- **`incoming.system_task` flag.** May not exist as a boolean today. Implementation must verify and add if absent; otherwise scheduled tasks could be hijacked by onboarding.
- **`rglob("SKILL.md")` side effects.** Could pick up stray SKILL.md files in skill subdirectories. Audit during implementation.
- **Personality drafted from preferences.** The personality skill derives `### Voice` and `### Boundaries` from the user's communication-style and length answers. If the user picks something off-axis ("aggressive"), the seed may need manual tweaking. Acceptable — the existing PR #107 override mechanism lets the user adjust by saying "be less X" in conversation.
- **Email channel UX.** 6 prompts × email round-trip = days. Acceptable; email is secondary and the flow still works there.
