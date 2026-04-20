# Skill Factory Rewrite — Design

**Issue:** [#23 — Add skill for authoring Curunir skills](https://github.com/jalemieux/curunir/issues/23)

**Date:** 2026-04-20

## Goal

Teach Curunir how to author **user-defined skills** that match project conventions, without a human having to reverse-engineer the format from `src/skills.py` or existing skills. The existing `skills/skill-factory/` is rewritten in place; it's currently Claude-Code-flavored and not specific to Curunir's runtime.

## Decisions

- **Two-tier skills model.** `skills/` holds system skills (maintainer-authored, committed, higher scrutiny). `context/skills/` holds user-defined skills (local customization, lower scrutiny).
- **Loader scans both directories.** On name collision, **system wins** and a warning is logged. Forces user to pick a non-colliding name.
- **Loader API: list of dirs.** `build_skill_manifest(skill_dirs: list[Path])` and `load_skill(name, skill_dirs: list[Path])`. Order of the list is priority order (first match wins on `load_skill`; first-seen wins in manifest dedup).
- **Missing user dir is silent.** If `context/skills/` doesn't exist, skip it — no warning. Common on fresh installs.
- **Rewrite `skills/skill-factory/` in place.** Replace SKILL.md and `references/template.md`. Delete `references/chub.md` (Claude Code only).
- **SKILL.md + one reference file.** No `scripts/` — user-defined skills have looser scrutiny, so a manual smoke-test checklist in SKILL.md is sufficient.

## Loader (`src/skills.py`)

### New signatures

```python
def build_skill_manifest(skill_dirs: list[Path]) -> str: ...
def load_skill(name: str, skill_dirs: list[Path]) -> str: ...
```

### Behavior

**`build_skill_manifest`:**
1. For each dir in `skill_dirs`, glob `*/SKILL.md`.
2. Parse frontmatter; keep entries with both `name` and `description`.
3. Dedupe by `name` — first-seen wins (so system wins when passed as `[system_dir, user_dir]`).
4. On a dedup drop, log `logger.warning("user skill '%s' shadowed by system skill at %s", name, system_path)`.
5. Render the same Markdown table as today, alphabetically by name. No source annotation in the table.

**`load_skill`:**
1. For each dir in `skill_dirs`, check `dir / name / SKILL.md`.
2. Return the first hit's contents.
3. If a shadowed user skill is requested by name, the system version is returned (consistent with manifest). Log the same warning.
4. If nothing found, return `f"Skill not found: {name}"` (unchanged).

### Call-site updates

`AgentConfig.skills_dir: Path` (`src/config.py:15`) is renamed to `AgentConfig.skill_dirs: list[Path]` and populated with `[Path("./skills"), Path("./context/skills")]` by default. All callers are updated:

| File | Current | After |
|---|---|---|
| `src/agent/system_prompt.py:17` | `build_skill_manifest(config.skills_dir)` | `build_skill_manifest(config.skill_dirs)` |
| `src/tools/skill_tool.py:7` | `load_skill(args["name"], config.skills_dir)` | `load_skill(args["name"], config.skill_dirs)` |
| `src/scheduler.py:102` | `load_skill(task["skill"], agent.config.skills_dir)` | `load_skill(task["skill"], agent.config.skill_dirs)` |
| `src/memory_extractor.py:75` | `config.skills_dir / "extract-learnings" / "SKILL.md"` | `load_skill("extract-learnings", config.skill_dirs)` — switch to the public API so shadowing rules apply consistently |

No new env var. No runtime toggle.

### Tests (`tests/test_skills.py`)

- Manifest merges skills from both dirs when both exist.
- Manifest works with only system dir (user dir missing — silent skip).
- Collision: system wins in both manifest and `load_skill`; warning is logged.
- `load_skill` returns user skill content when no collision.
- `load_skill` returns `"Skill not found"` for unknown name.

## `skills/skill-factory/SKILL.md`

Full rewrite, ~120 lines. Sections:

### Frontmatter

```yaml
---
name: skill-factory
description: Use when the user asks to create, write, or build a new user-defined skill — produces a SKILL.md in context/skills/ matching Curunir conventions
---
```

### §1 — What you're creating

Two sentences: user-defined skills live in `context/skills/<kebab-name>/SKILL.md`, are loaded alongside system skills at startup, and need a restart to appear in the manifest. System skills in `skills/` are maintainer-authored — don't write there.

### §2 — Workflow (flat, 4 steps)

1. **Clarify:** kebab-case name, trigger condition (when should the agent load this), what tools it needs from the opt-in set.
2. **Conflict check:** `context/skills/<name>/` must not already exist. If `skills/<name>/` exists, pick a different name — system wins.
3. **Generate:** copy `references/template.md`, fill in frontmatter + body. Add `references/`, `scripts/`, or `templates/` only if SKILL.md would otherwise exceed ~200 lines.
4. **Smoke test:** frontmatter parses (no YAML errors, `name` matches directory); `load_skill <name>` via the tool returns content and not `"Skill not found"`; dry-run the trigger scenario mentally (would the agent actually load this when the user says X?).

### §3 — Frontmatter reference

Inline table:

| Field | Required | Notes |
|---|---|---|
| `name` | yes | kebab-case, must match parent directory |
| `description` | yes | trigger phrasing — "Use when …" |
| `tools` | no | comma-separated opt-in tools (currently only `attach`) |

### §4 — Writing good descriptions

Two bullets: describe WHEN the skill should trigger, not WHAT it contains. Include trigger phrases the user is likely to say. One good/bad pair.

### §5 — Opt-in tools

Current list: `attach`. Declared via `tools: attach` in frontmatter. New opt-in tools are registered in `src/tools/schemas.py` — pointer to `src/tools/README.md`.

### §6 — Supporting files

When to add `references/` (large reference material), `scripts/` (executable helpers the agent invokes rather than reimplements), `templates/` (boilerplate). Default is SKILL.md-only.

### §7 — Restart caveat

The skill manifest is built at startup. A newly-written skill won't appear in the current session's manifest until Curunir restarts. However, `load_skill <name>` will succeed mid-session because it reads from disk at call time. Full discovery requires restart.

### §8 — Common mistakes

- Describing contents instead of triggers in `description`.
- Writing to `skills/` instead of `context/skills/`.
- Directory name doesn't match frontmatter `name`.
- Declaring tools in `tools:` that aren't registered as opt-in (only `attach` today).
- Assuming the new skill will auto-appear in the manifest without a restart.

## `skills/skill-factory/references/template.md`

~40 lines. Structure:

1. One-line instruction: copy to `context/skills/<your-skill-name>/SKILL.md` and fill in.
2. A fenced block with the SKILL.md skeleton (frontmatter + Title + one-paragraph intro + Workflow + Reference/Tips/Examples + Common Mistakes).
3. A good-vs-bad `description` example.
4. A short note on when to add supporting files (references/, scripts/, templates/) — defaults to none.

No meta-commentary or schema essays. The file is scaffolding, not a tutorial.

## Files to delete

- `skills/skill-factory/references/chub.md` — Claude Code specific (Context Hub CLI), not applicable to Curunir.

## Verification

1. `pytest tests/test_skills.py` passes (new collision + two-dir cases).
2. Existing test suite passes — changing `build_skill_manifest`/`load_skill` signatures is a breaking internal change, so all call sites must be updated.
3. Manual: create a throwaway `context/skills/hello-world/SKILL.md` with minimal frontmatter, restart, confirm it appears in the manifest, confirm `load_skill hello-world` via the running agent returns its content.
4. Manual: create a colliding name in `context/skills/` matching an existing system skill (e.g. `skill-factory`). Restart. Confirm the warning fires and the system version is served.

## Non-goals

- No eval/benchmark infrastructure (unlike the official `skill-creator`). User-defined skills are local customization; iteration happens by hand.
- No hot reload of the manifest. Restart-on-change is the accepted workflow.
- No UI for managing user skills. They're plain files under `context/skills/`.
- No migration path for the deleted `chub.md` — it was never usable in Curunir.
