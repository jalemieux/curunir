---
name: onboarding
description: "Use on a not-yet-onboarded user's first message (the agent loop auto-triggers this when `context/identity.md` is absent) or when the user types `/onboarding` to redo setup. Runs profile → preferences → personality; the personality step writes `context/identity.md`, which is what marks onboarding complete."
---

# Onboarding

Run the three sub-skills in order. The personality step writes `context/identity.md` — its presence is the completion signal (no separate marker). Total budget: 6 user prompts (2 per sub-skill). Stay terse — onboarding is a quick setup, not a tour.

## When to use

- The agent loop auto-invokes you when an un-onboarded user sends their first message (it detects this by `context/identity.md` being absent).
- The user typed `/onboarding` and wants to redo setup.

## First step — check for prior onboarding

Check whether `context/identity.md` exists (e.g. `bash` `test -f context/identity.md`). If it exists, ask: "You're already set up. Redo everything, or just one section — profile, preferences, or personality?"

- "everything" or "redo all" → continue with the full flow below.
- one section → `load_skill` that one section, follow it, exit when it returns.
- "cancel" or anything ambiguous → say "OK, leaving things as they are." and stop.

If `context/identity.md` does NOT exist, proceed to the full flow.

## Full flow

1. **Profile.** Run `load_skill` with `name=profile`. Follow its instructions to completion — it will ask 2 questions and write `context/memory/profile.md`.
2. **Preferences.** Run `load_skill` with `name=preferences`. Follow it to completion — 2 questions, writes `context/memory/preferences.md`.
3. **Personality.** Run `load_skill` with `name=personality`. Follow it to completion — 2 questions, edits `context/identity.md`. The personality skill also fills the opening sentence and derives the `### Personality` block from the profile + preferences answers earlier in this conversation. Writing this file is what completes onboarding, so run it last.
4. **Confirm.** Reply: "All set. You can re-run any section anytime with `/profile`, `/preferences`, or `/personality`."

## Rules

- Ask one question at a time. Never bundle.
- Trust the user's first answer. Don't ask follow-ups unless the answer is literally unparseable.
- If a write tool fails mid-flow, surface the error to the user once and stop. As long as `context/identity.md` was never written, the next message re-triggers the gate.
