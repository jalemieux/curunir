---
name: profile
description: "Use to onboard or refresh the owner's profile facts — name and role/focus. Triggered by `/profile` or by the `onboarding` orchestrator. Writes the container's shared profile.md."
---

# Profile

Capture two facts about the owner: how to address them, and what they want help with. These end up in `{{shared}}/profile.md`, the one user profile every agent in this container reads, and are read by future turns.

Only the container's default agent may write that file. If you are not the default agent (the setup note says so, or your `write` is refused), still ask the two questions but skip the write: tell the user their profile is kept by the default agent and will be filled in from this conversation. Don't write the facts anywhere else.

## When to use

- The `onboarding` orchestrator hands off here as step 1.
- The user typed `/profile` and wants to redo just the profile section.

## Conversation

Ask exactly two questions, one at a time, waiting for the user's reply between each. Keep them short.

1. **Name and form of address.** "What should I call you? Any preferred form — nickname, title, first name only?"
2. **Role and focus.** "One line: what do you do, or what do you most want my help with?"

Don't add follow-ups. If the user gives a short or terse answer, accept it — they can edit the file later.

## Write

After the second answer, write `{{shared}}/profile.md` with the `write` tool (overwriting whatever is there — the bootstrap default is a placeholder).

The file must contain two H2 sections in this exact shape:

```
<!--
Owner identity facts only — name, role, focus. Edit anytime; this file is
read into context on every turn.
-->

# Owner Profile

## Name

**Source:** onboarding - <YYYY-MM-DD>
**Fact:** <how the user wants to be addressed, verbatim or lightly cleaned up>
**Context:** Used in greetings and direct address.

## Role / Focus

**Source:** onboarding - <YYYY-MM-DD>
**Fact:** <one-line role / what they want help with>
**Context:** Anchors the kinds of tasks this assistant is for.
```

Use today's date in UTC for `<YYYY-MM-DD>`. Run `bash` with `date -u +%Y-%m-%d` and use the output verbatim.

## Return

After the write succeeds (or, when you are not the default agent, after telling the user where the profile is kept), output a one-liner like "Got it — saved." and stop. The orchestrator will pick up from there.
