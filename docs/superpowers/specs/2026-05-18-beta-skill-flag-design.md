# Design: `beta` skill flag

**Date:** 2026-05-18
**Status:** Approved

## Problem

Every enabled skill appears in the "Available Skills" table that
`build_skill_manifest()` bakes into the system prompt. This has two costs:

1. **No beta channel.** A new skill is either fully visible to the agent
   (and routed to spontaneously) or `disabled: true` (invisible and
   uninvokable). There is no in-between for testing a skill before GA.
2. **Catalog growth.** The manifest grows with every skill, consuming
   system-prompt tokens whether or not the skill is in regular use.

## Solution

A per-skill frontmatter flag, `beta: true`, that excludes a skill from the
manifest **only**. The skill stays in the registry, so it remains loadable
and slash-forceable — the agent just won't route to it on its own.

```yaml
---
name: medical-research
description: ...
beta: true
---
```

### Behavior matrix

| Surface | Function | Beta skill visible? |
|---------|----------|---------------------|
| Agent system prompt | `build_skill_manifest()` | **No** — filtered out |
| `load_skill` tool | `load_skill()` | Yes — loadable by name |
| `/beta-skill` slash | `slash_commands.py` | Yes — forceable |
| `/skills`, `/help` | `slash_commands.py` | Yes — unchanged (human-facing discovery) |
| Portal picker | `portal_skill_list()` | Unchanged — already `portal_summary`-gated |

A beta skill is therefore: invisible to the agent's spontaneous routing,
but fully usable when explicitly invoked by a human (`/beta-skill`) or when
the agent is told its name.

## Changes (`src/skills.py`)

1. `Skill` dataclass gains `beta: bool = False`.
2. `load_registry()` parses the `beta` frontmatter field (same truthy set as
   `disabled`: `true`/`1`/`yes`/`on`) and sets it on the `Skill`. The skill
   is **still included** in the registry.
3. `build_skill_manifest()` skips skills where `beta is True`. If filtering
   leaves zero skills, behave as the existing empty-registry case (return
   `""`).

No other modules change. `/skills`, `/help`, `load_skill`, dispatch, and
`portal_skill_list` are deliberately left as-is.

## Testing (`tests/test_skills.py`)

- A `beta: true` skill is **absent** from `build_skill_manifest()` output.
- The same skill **is present** in `load_registry()`.
- The same skill is loadable via `load_skill()` by name.
- A non-beta skill alongside it still appears in the manifest.

## Out of scope

- Portal "push to prompt" button (dropped — slash-forcing covers testing).
- Filtering beta skills from `/skills` or `/help`.
- Per-session dynamic manifest injection.

## Follow-up: explicit invocation (#204)

The `beta` flag was later renamed `hidden` (#207). Issue #204 surfaced a gap:
because a hidden skill never appears in the system-prompt "Available Skills"
catalog, the agent would conclude the skill didn't exist when explicitly
invoked — even though `load_skill` and the slash dispatcher both still
resolve it. The plumbing was correct; the agent's *awareness* was not.

Fix is behavioral, not structural — the manifest still omits hidden skills:

- The slash-forcing synthetic prompt is now imperative: it tells the agent
  to call `load_skill` with the exact name and notes the skill may be absent
  from "Available Skills" but is still loadable.
- The `load_skill` tool description states the "Available Skills" table is
  not exhaustive — any skill named by exact name is loadable.

This is a soft lever (prompt wording), not an enforced guarantee; a hard
guarantee would require dynamic manifest injection, still out of scope.
