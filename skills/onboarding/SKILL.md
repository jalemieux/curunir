---
name: onboarding
description: "Use on a not-yet-onboarded user's first message (the agent loop auto-triggers this) or when the user types `/onboarding` to redo setup. Runs profile → preferences → personality and writes `context/.onboarded` when done."
---

# Onboarding

Run the three sub-skills in order, then write the marker. Total budget: 6 user prompts (2 per sub-skill). Stay terse — onboarding is a quick setup, not a tour.

## When to use

- The agent loop auto-invokes you when an un-onboarded user sends their first message.
- The user typed `/onboarding` and wants to redo setup.

## First step — check for prior onboarding

Read `context/.onboarded`. If it exists, ask: "You're already set up. Redo everything, or just one section — profile, preferences, or personality?"

- "everything" or "redo all" → continue with the full flow below.
- one section → `load_skill` that one section, follow it, exit when it returns. Do NOT touch `.onboarded` in this branch.
- "cancel" or anything ambiguous → say "OK, leaving things as they are." and stop.

If `.onboarded` does NOT exist, proceed to the full flow.

## Full flow

1. **Profile.** Run `load_skill` with `name=profile`. Follow its instructions to completion — it will ask 2 questions and write `context/memory/profile.md`.
2. **Preferences.** Run `load_skill` with `name=preferences`. Follow it to completion — 2 questions, writes `context/memory/preferences.md`.
3. **Personality.** Run `load_skill` with `name=personality`. Follow it to completion — 2 questions, edits `context/identity.md`. The personality skill also fills the opening sentence and derives the `### Personality` block from the profile + preferences answers earlier in this conversation.
4. **Marker.** After all three writes succeed, use `bash` to run `touch context/.onboarded`. (Or use `write` to create the file with empty content — either works.)
5. **Confirm.** Reply: "All set. You can re-run any section anytime with `/profile`, `/preferences`, or `/personality`."

## Rules

- Ask one question at a time. Never bundle.
- Trust the user's first answer. Don't ask follow-ups unless the answer is literally unparseable.
- If a write tool fails mid-flow, surface the error to the user once and stop — do not write `.onboarded`. The next message will re-trigger the gate.
