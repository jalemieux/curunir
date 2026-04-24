# SKILL.md Template

Copy this file to `context/skills/<your-skill-name>/SKILL.md` and fill in
the placeholders. Delete sections you don't need. Keep the whole file under
~200 lines; move long content into `references/` files that SKILL.md links
to.

## Skeleton

```markdown
---
name: <kebab-case-name>
description: "Use when <trigger condition — user phrase or situation>"
# tools: attach       # uncomment if you need an opt-in tool
---

# <Human Title>

<One paragraph: what the skill does and when the agent should reach for it.>

## Workflow

1. <First step — usually "ask the user for X" or "check state Y">.
2. <Second step — the actual operation>.
3. <Final step — confirm / report / deliver>.

## Reference

<Commands, parameters, shell snippets. Keep concrete — the agent copies
these verbatim, not reconstructs them.>

## Common mistakes

- <Gotcha 1 — the kind of thing the agent would get wrong without help>.
- <Gotcha 2>.
```

## Writing a good `description`

The description is the trigger, not a summary. Aim for phrases a user would
actually say.

- Good: `"Use when the user asks to research a topic in depth or produce a
  report with citations"`
- Bad: `"A skill for doing deep research on topics"`

## When to add supporting files

Default to SKILL.md only. Add a subdirectory when:

- `references/` — you have a large API reference, schema, or how-to that
  would bloat SKILL.md past ~200 lines. SKILL.md links to it by filename.
- `scripts/` — there's a non-trivial shell or Python helper the agent should
  invoke as-is (e.g. an API key setup flow). Easier to debug than inlining.
- `templates/` — boilerplate the agent copies and fills in (e.g. a report
  skeleton).

If in doubt, don't add the directory. You can always split it out later.
