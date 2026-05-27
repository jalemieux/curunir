---
name: identity
description: "Use when the user wants to view, change, or shape this agent's identity / persona. Triggered by `/identity` in the CLI or web UI. Always shows the current `context/identity.md` first and confirms any proposed edit before writing."
---

# Identity

The agent's persona is the contents of `context/identity.md`. It is read
verbatim into every system prompt, so edits take effect on the next turn —
no restart needed. This skill helps the user inspect and edit it safely.

## When to use

The user typed `/identity` (with optional follow-up text describing what
they want changed), or asked questions like "edit your persona", "change
your tone", "what's your identity file say". The skill drives a small
conversational loop: **show** the current file, **propose** a specific
edit, **confirm** with the user, then **apply** with `edit`.

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

### 1. Show

Read `context/identity.md`. If it's missing, surface the onboarding pointer
above and stop. Otherwise, emit the current contents to the user using
this exact shape, then **end the turn**:

````
Here's your current identity file:

```markdown
<full contents of context/identity.md>
```

What would you like to change? I can also suggest tweaks if you tell me
the goal (e.g. "more terse", "less formal", "add a Capability for X").
````

Do not call `edit` in this turn. The point of the gate is that the user
sees the current state before being asked what to change.

### 2. Propose

Once the user describes a change (or asks for a suggestion), formulate
the specific edit and emit it for review:

- **Multi-line changes**: show a unified diff (``` ```diff ``` fenced) so
  the user can see exactly which lines move.
- **One- or two-line tweaks**: show `before:` / `after:` blocks instead —
  a diff for two lines is more noise than signal.

End the proposal turn with this exact line:

```
Apply this? (yes / revise / cancel)
```

Then **stop**. No `edit` call in the proposal turn.

### 3. Confirm gate

Do **not** call `edit` until the user affirms. Affirmative replies:
`yes`, `apply`, `looks good`, `ship it`, 👍, `go ahead`, or any clearly
positive answer to the apply prompt.

- On `revise` or any substantive reply (e.g. "keep the X line", "make Y
  shorter too") → re-formulate the diff incorporating the feedback and
  re-emit. Between iterations, make no other changes — only the proposed
  edit moves. Re-ask `Apply this? (yes / revise / cancel)` and stop again.
- On `cancel` / `nevermind` / `drop it` → acknowledge in one line, do not
  write.
- On a question about the proposal → answer it, re-emit the diff if you
  changed anything material, and stop.

### 4. Apply

Once confirmed, call `edit` against `context/identity.md` with the
narrow change you proposed. Prefer narrow edits over wholesale rewrites
— keep the user's voice. Echo the final before/after snippet (or the
applied hunk for larger diffs) so the user sees what landed.

## Skipping the confirm gate

Skip Steps 1–3 and go straight to Step 4 **only** when the original
`/identity` invocation contained both the concrete change *and* an
explicit go-ahead phrase — e.g. `/identity drop the Deliverables section,
just do it`, `/identity rewrite the Personality section to be terser — no
need to confirm`, `/identity ... skip the confirm`. The change must be
specific enough to apply without further clarification.

When skipping, announce it in one line before calling `edit`:

```
Applying directly — you said skip the confirm.
```

Default is always confirm. If in doubt, do not skip. The cost of one
extra turn is small; the cost of silently rewriting someone's persona is
large.

## Style notes

- The file is markdown. Top-level headings are conventional (Core Traits,
  Capabilities, Guidelines, Memory, Scheduling, Creating Skills) — keep
  them when present, don't reorganize unless asked.
- Persona changes are subjective; surface tradeoffs ("terser will drop the
  preambles you sometimes use to explain reasoning — keep that?") rather
  than guessing.
- Do not modify `context.default/identity.md` — that's the shipped default,
  not the user's persona.
- Never write a stub `context/identity.md` from inside this skill — the
  onboarding flow is the only legitimate way to create it.
