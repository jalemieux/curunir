---
name: skill-factory
description: "Use when the user asks to create, write, or build a new user-defined skill — produces a SKILL.md in context/skills/ matching Curunir conventions"
---

# Skill Factory

## What you're creating

User-defined skills live in `context/skills/<kebab-name>/SKILL.md` and are
loaded alongside system skills at startup. System skills in `skills/` are
maintainer-authored and committed with the repo — **do not write there**. On
a name collision between `skills/` and `context/skills/`, the system version
wins and a warning is logged.

## Workflow

1. **Clarify.** Ask 1–2 questions to pin down:
   - The skill name (kebab-case, letters/numbers/hyphens only — the directory
     name IS the skill name).
   - The trigger condition: what should the user say or what state should the
     agent be in for this skill to load?
   - Any opt-in tools it needs (see §5).
2. **Conflict check.** `context/skills/<name>/` must not already exist. If
   `skills/<name>/` exists, pick a different name — system wins on collision.
3. **Generate.** Copy `references/template.md`, fill in the frontmatter and
   body. Add `references/`, `scripts/`, or `templates/` subdirectories only
   if SKILL.md would otherwise exceed ~200 lines (see §6).
4. **Smoke test.** Three checks, all required:
   - Frontmatter parses (no YAML errors) and `name` matches the directory.
   - `load_skill` on the new name returns content, not `"Skill not found"`.
   - Dry-run the trigger scenario mentally: if the user said the phrase
     you put in `description`, would the manifest naturally route the agent
     to this skill? If not, rewrite the description.

## Frontmatter reference

| Field         | Required | Notes                                                         |
|---------------|----------|---------------------------------------------------------------|
| `name`        | yes      | kebab-case, must match parent directory                       |
| `description` | yes      | trigger phrasing — "Use when …"                               |
| `tools`       | no       | comma-separated opt-in tools (currently only `attach`)        |
| `disabled`    | no       | `true` to hide from manifest and block `load_skill`           |

## Writing good descriptions

The `description` is the sole signal the agent uses to decide whether to load
a skill. Describe **when to trigger**, not what the skill contains.

- Good: `"Use when the user pastes meeting notes or a Slack catch-up and
  asks for durable takeaways"`
- Bad: `"A skill that extracts learnings from text"`

Include the kinds of phrases a user is likely to say — "fact-check this",
"research X", "draft an email to Y" — so the agent recognizes them.

## Opt-in tools

Opt-in tools are extra tools a skill unlocks when loaded. Declare them in
frontmatter:

```yaml
---
name: my-skill
description: Use when ...
tools: attach
---
```

Currently only `attach` is available (delivers a file as an email
attachment / CLI file path). New opt-in tools are registered in
`src/tools/schemas.py` — see `src/tools/README.md` for the mechanics. Don't
declare a tool that isn't registered; it won't do anything.

## Supporting files

A skill is SKILL.md by default. Add subdirectories only when the skill is
big enough to warrant them:

- `references/` — large reference material the agent reads on demand (API
  reference tables, schemas, long how-tos).
- `scripts/` — executable helpers the agent invokes rather than
  reimplementing inline (e.g. a credential-setup script).
- `templates/` — boilerplate the agent copies and fills in.

If SKILL.md stays under ~200 lines and doesn't repeat itself, you don't
need any of these.

## Restart caveat

The skill manifest is built once at startup. A newly-written SKILL.md **will
not appear in the current session's manifest** until Curunir restarts.
However, `load_skill <name>` reads from disk at call time — so if the user
names the new skill directly, it will load in the current session. Full
discovery (the agent picking it up on its own from trigger phrases) requires
a restart.

## Common mistakes

- **Describing contents instead of triggers** in `description`. If it reads
  like a summary, rewrite it.
- **Writing to `skills/` instead of `context/skills/`.** System skills are
  maintainer-authored.
- **Directory name doesn't match frontmatter `name`.** The loader keys on
  `name`, but humans browse by directory — mismatches cause confusion.
- **Declaring tools that aren't registered.** Only `attach` is currently
  available as opt-in.
- **Assuming the new skill auto-appears.** The manifest is built at startup;
  a restart is needed for the agent to discover the skill on its own.
- **Skipping the smoke test.** A skill with broken frontmatter silently
  disappears from the manifest — verify it loads before calling the job done.
