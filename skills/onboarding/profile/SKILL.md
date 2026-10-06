---
name: profile
description: "Use to onboard or refresh the owner's profile facts — name and role/focus. Triggered by `/profile` or by the `onboarding` orchestrator. Writes the container's shared profile.md."
---

# Profile

Capture two facts about the owner: how to address them, and what they want help with. These end up in `{{shared}}/profile.md`, the one user profile every agent in this container reads, and are read by future turns.

Only the container's default agent may write that file. If the orchestrator sent you to **Profile already set up**, or you are not the default agent (the setup note says so, or your `write` is refused), follow that section instead of Conversation and Write.

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

## Profile already set up

The shared profile is in your context. Don't re-ask name or role, and never write `{{shared}}/profile.md` from here.

Ask one question: "Your profile is already set up. Is there anything you want me, specifically, to know that isn't in it?" Trust the first answer. If they have nothing to add, write nothing.

Otherwise save it to `{{context}}/memory/user.md`, your own notes about the user, with the `write` tool. If the file exists, `read` it first and keep what is there.

```
# User Notes

What this agent knows about the user beyond the shared profile.

## <short topic>

**Source:** onboarding - <YYYY-MM-DD>
**Fact:** <what the user said, verbatim or lightly cleaned up>
```

If the shared profile is still empty (the user reached you before the default agent), ask the two questions under Conversation instead and save the answers to `{{context}}/memory/user.md` in the same shape.

## Return

After the write succeeds (or the user had nothing to add), output a one-liner like "Got it — saved." and stop. The orchestrator will pick up from there.
