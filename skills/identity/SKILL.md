---
name: identity
description: "Use when the user wants to view, change, or shape this agent's identity / persona. Triggered by `/identity` in the CLI or web UI. The identity lives at `context/identity.md` and seeds every system prompt."
---

# Identity

The agent's persona is the contents of `context/identity.md`. It is read
verbatim into every system prompt, so edits take effect on the next turn —
no restart needed. This skill helps the user inspect and edit it safely.

## When to use

The user typed `/identity` (with optional follow-up text describing what
they want changed), or asked questions like "edit your persona", "change
your tone", "what's your identity file say". The skill drives a small
conversational loop: show the current file, confirm the change, apply it
with `edit`, show a diff.

## Resolving the file

`context/identity.md` is the only canonical location. If it does NOT
exist, the user is on a fresh install — point them at the onboarding flow:

```
onboarding/README.md walks through filling out onboarding/questions.md
and generating context.default/identity.md from those answers. `bootstrap.py`
copies that into context/identity.md on the next launch.
```

Do not silently create a stub `context/identity.md`. The onboarding flow
exists because a thoughtful persona matters more than a placeholder.

## Workflow

1. `read context/identity.md` — show the current contents (or surface the
   onboarding pointer above if missing).
2. If the user's `/identity` invocation already carried instructions (e.g.
   `/identity make it terser`), proceed straight to step 3. Otherwise ask
   what they'd like to change.
3. Use `edit` to apply the change. Prefer narrow edits over wholesale
   rewrites — keep the user's voice.
4. Echo back the relevant before/after snippet so the user can see exactly
   what changed.

## Style notes

- The file is markdown. Top-level headings are conventional (Core Traits,
  Capabilities, Guidelines, Memory, Scheduling, Creating Skills) — keep
  them when present, don't reorganize unless asked.
- Persona changes are subjective; surface tradeoffs ("terser will drop the
  preambles you sometimes use to explain reasoning — keep that?") rather
  than guessing.
- Do not modify `context.default/identity.md` — that's the shipped default,
  not the user's persona.
