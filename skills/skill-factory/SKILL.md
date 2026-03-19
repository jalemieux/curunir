---
name: skill-factory
description: "Use when asked to create, write, or build a new skill — gathers requirements, finds docs, generates SKILL.md, and smoke tests it"
---

# Skill Factory

Create a new skill from scratch. Walks you through: requirements → research → generate → smoke test.

**CLI dependencies (available in Docker container):**
- `chub` — Context Hub CLI for fetching curated API docs. Optional; skill falls back to web search if unavailable.

## Workflow

### Phase 1 — Requirements

1. User describes the skill they want.
2. Identify the **skill category** (see Category Heuristic below) to shape template emphasis.
3. Validate the skill name: lowercase-kebab-case, letters/numbers/hyphens only. The directory name IS the skill name.
4. Ask 1-2 clarifying questions: scope, which operations, any gotchas.
5. **Installation destination** — ask where to save the skill. Offer:
   - **Project-local** (`./skills/`) — committed with the repo, available to collaborators. Default if the project already has a `skills/` directory.
   - **User-global** (`~/.claude/skills/`) — available across all projects for this user.
   - **Custom path** — user specifies.
   Use the chosen path as `{skills_dir}` for the rest of the workflow.
6. Propose a **success criteria / test scenario** the skill can be verified against. User can override.

### Phase 2 — Research

Research priority: user-provided docs > context hub (`chub`) > web search > agent knowledge.

7. Gather docs using the priority order above. If `chub` is available (`which chub`), use it — see `references/chub.md` for the search → get → annotate workflow. Do not install tools that aren't available.
8. Read 1-2 existing skills from the project's skills directory as style reference. If none exist, use the template as sole reference.

### Phase 3 — Generate

9. **Conflict check** — verify `{skills_dir}/{skill-name}/` doesn't already exist. If it does, ask the user whether to overwrite or choose a different name.
10. Assess whether the skill needs supporting files (see Directory Structure below).
11. **Credential check** — if the skill uses APIs that require keys or tokens, generate an interactive setup script in `scripts/` that helps the user obtain and configure them. The script should: check if already set, tell the user where to sign up, prompt for the key, validate it against the API, and offer a choice of where to persist it (shell config, project `.env`, custom path, or session-only). Reference the script from SKILL.md's prerequisites.
12. Generate SKILL.md using the template. Read `references/template.md` for the full template and category heuristic.
13. Generate any supporting files (scripts, references, templates, examples).
14. Write to `{skills_dir}/{skill-name}/`.

### Phase 4 — Smoke Test

15. **Structural validation** — frontmatter parses correctly, `name` and `description` present, file at correct path.
16. **Load test** — use the skill loading tool available in your environment to load the new skill by name. Verify it returns the skill content, not a "not found" error.
17. **Dry-run against success criteria** — walk through the skill's instructions for the test scenario. Execute read-only steps. For skills that call external APIs, compose the commands but do not execute them. Stop before any mutating side effects.
18. Report pass/fail. If fail, fix and re-test.

## Template & Category Heuristic

Read `references/template.md` for the full SKILL.md template and the 9-category heuristic table. The template has sections: frontmatter, title, Usage, Examples, Parameters/Reference, Tips, Common Mistakes. Scale each section to complexity.

## Directory Structure

A skill can be more than SKILL.md. Add supporting directories only when complexity warrants it:

- `scripts/` — reusable scripts the agent runs, not reconstructs
- `references/` — large reference docs, schemas, param tables
- `templates/` — boilerplate the agent copies/adapts
- `examples/` — sample inputs/outputs clarifying expected formats

SKILL.md references supporting files explicitly. Simple skills are SKILL.md only.

## Writing Good Descriptions

The `description` determines skill discovery. Describe the **trigger condition**, not the contents.

- Good: `"Use when processing Slack catch-ups or incident summaries to extract durable knowledge"`
- Bad: `"A skill for extracting learnings from text"`

## Common Mistakes

- **Describing contents instead of triggers** — the `description` field must say WHEN to load the skill, not summarize what it does. If the description reads like a summary, rewrite it.
- **Stating the obvious** — the agent knows how to code. Don't explain curl, jq, or basic patterns. Focus on API quirks, org-specific knowledge, and things that change default behavior.
- **Skipping the conflict check** — always verify the skill directory doesn't already exist before writing. Overwriting an existing skill without asking is destructive.
- **Not reading existing skills** — always read 1-2 existing skills for style calibration before generating. Match the project's conventions.
- **Supporting files by default** — only add scripts/, references/, etc. when complexity warrants it. Most skills are SKILL.md only.
- **Skipping the smoke test** — always validate: frontmatter parses, skill loads by name, dry-run against the test scenario passes.
- **Hardcoding environment-specific tool names** — use whatever skill loading / searching tools are available in the current environment. Don't assume specific tool names.
