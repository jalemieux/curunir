---
name: preferences
description: "Use to onboard or refresh the owner's working preferences — communication style and response length. Triggered by `/preferences` or by the `onboarding` orchestrator. Writes `context/memory/preferences.md`."
---

# Preferences

Capture two facts about how the owner wants to be communicated with. These end up in `context/memory/preferences.md` and shape every response.

## When to use

- The `onboarding` orchestrator hands off here as step 2.
- The user typed `/preferences` and wants to redo just this section.

## Conversation

Ask exactly two questions, one at a time, waiting for each reply.

1. **Communication style.** "How do you like to be communicated with? Formal, casual, blunt, warm — or some mix? Anything specific I should avoid?"
2. **Response length.** "Default response length — terse, balanced, or detailed?"

Accept short answers. No follow-ups unless the user gave an unparseable response (e.g., "I dunno"), in which case offer a one-sentence concrete choice and move on.

## Write

After the second answer, write `context/memory/preferences.md` with `write` (overwriting any existing default).

```
<!--
Owner's working and communication preferences. Edit anytime; read into
context on every turn.
-->

# Owner Preferences

## Communication style

**Source:** onboarding - <YYYY-MM-DD>
**Fact:** <user's answer, lightly cleaned up>
**Context:** Applies to every reply.

## Response length

**Source:** onboarding - <YYYY-MM-DD>
**Fact:** <terse|balanced|detailed — match the user's choice>
**Context:** Default reply length. Overridable per-turn by explicit ask.
```

Use today's date (UTC) — same source as in the `profile` skill.

## Return

After the write succeeds, say "Got it — saved." and stop. The orchestrator will continue.
