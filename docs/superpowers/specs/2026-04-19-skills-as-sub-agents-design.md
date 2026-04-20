# Skills as Sub-Agents — Small-Model Design

**Date:** 2026-04-19
**Status:** Draft
**Supersedes:** Specialist/skill separation in `2026-04-09-small-model-orchestrator-design.md`

## Motivation

In the current small-model orchestrator (`ORCHESTRATOR_MODE=true`), skills are disabled entirely. The orchestrator routes via pre-defined specialists in `agents.yaml` — but those specialists are mostly tool-buckets (`files` = tools that touch files, `system` = bash, etc.), not procedures. Procedural knowledge that the big-model version carries in skills — TDD workflow, git-contribute lifecycle, research methods, debugging discipline — has no home in the small-model version.

The result: small-model Curunir can route by capability but cannot apply procedural knowledge to a task. Complex recurring work has no corresponding mechanism. This is a real gap in the agency of the small-model version.

## Principles

1. **Orchestrator context is precious.** It persists across the whole session. Every token and every tool output stays visible unless explicitly trimmed.
2. **Sub-agent context is disposable.** It can bloat freely within one task, then discard everything except a tight summary.
3. **Skills belong in sub-agents.** Procedural knowledge lands in a throwaway context, not the orchestrator's long-lived one.
4. **One skill per sub-agent invocation.** Multiple skills per sub-agent reintroduces the bloat problem we are avoiding.
5. **Skills are earned.** A named skill exists for recurring patterns or heavy work; one-off unstructured tasks stay with the orchestrator's direct tools.

## Design

### Skills replace specialists

The current two-tier abstraction (`agents.yaml` specialists + `skills/` procedures) collapses into one concept. A skill defines a sub-agent type. Its frontmatter carries what `agents.yaml` used to carry (tool allowlist, iteration cap); its body is the sub-agent's system prompt.

`agents.yaml` is removed. `skills/{name}/SKILL.md` is the only source of sub-agent definitions in orchestrator mode.

### Skill frontmatter

```yaml
---
name: git-contribute
description: Pick up a GitHub issue, implement a fix, shepherd a PR to merge
tools: [bash, read, edit, write, grep, glob]
max_iterations: 20
---

# Skill body — becomes the sub-agent system prompt

You are a GitHub contributor specialist. ...
```

Required fields:

- `name` — unique identifier
- `description` — one-line "when to use this skill"; shown to the orchestrator in the skill manifest
- `tools` — allowlist of tool names the sub-agent can call
- `max_iterations` — sub-agent iteration cap

Optional fields:

- `max_output_tokens` — ceiling for the sub-agent's returned response. Skills that produce long outputs (research reports, code reviews, summaries) declare a higher value here. Defaults to 2000 tokens if omitted. Truncation is a runaway-safety net, not a shape constraint — pick a number that fits the longest legitimate output the skill produces.

The body below the frontmatter becomes the sub-agent's system prompt. For small-model mode, target 500-1500 tokens of body.

### Orchestrator

The orchestrator has:

- **Direct tools:** `read`, `edit`, `write`, `bash`, `grep`, `glob`, `web_fetch`, `schedule` — for unstructured one-off work that does not match a named skill.
- **Skill manifest** in its system prompt: a name+description table of all available skills.
- **`run_skill` tool** to invoke a skill-backed sub-agent.

A principle baked into the orchestrator prompt: *"Prefer `run_skill` for anything likely to produce large output, require more than ~3 tool calls, or match a named skill's description. Use direct tools only for quick unstructured tasks."*

The orchestrator's system prompt is assembled from:

1. `context/identity.md` (persona and general guidelines)
2. The delegation principle above
3. The skill manifest — a Markdown table of `name | description` for every skill in `skills/`

No `agents.yaml`-derived specialist table appears, because specialists are gone.

### `run_skill` tool

```json
{
  "name": "run_skill",
  "description": "Run a skill in a fresh sub-agent context. The sub-agent sees only the skill's system prompt, its declared tools, and the task+intent below. Returns the sub-agent's final response.",
  "parameters": {
    "skill": {
      "type": "string",
      "description": "Name of the skill to run (must match an entry in the skill manifest)"
    },
    "task": {
      "type": "string",
      "description": "The concrete action the sub-agent should perform"
    },
    "intent": {
      "type": "string",
      "description": "What you need back — the user's goal, not a restatement of the task"
    }
  }
}
```

Dispatcher behavior on call:

1. Look up skill by name. Return error if not found.
2. Spawn a new `Agent` with:
   - System prompt = skill body (frontmatter stripped)
   - Tool allowlist = skill's `tools`
   - Iteration cap = skill's `max_iterations`
3. Feed task + intent as the first user message.
4. Run to completion or iteration cap.
5. Return sub-agent's final response to the orchestrator. If the response exceeds the skill's `max_output_tokens` (or the 2000-token default), truncate with an explicit marker (`... [truncated: N tokens omitted]`) so the orchestrator can see it was cut. Truncation exists only to prevent runaway outputs — the normal case is that the sub-agent's full response is returned intact.

### No freeform fallback

Every `run_skill` call requires a skill name that exists in the manifest. There is no "delegate with ad-hoc tools" variant. If no skill fits a task, the orchestrator handles it with its direct tools.

This keeps the delegation surface unambiguous: one tool, one mode, one menu.

### Sub-agents cannot delegate

A sub-agent does not receive `run_skill` in its tool set, regardless of what its skill's `tools` allowlist contains. No recursive skill invocation, no chaining. If work needs multiple skills, the orchestrator runs them sequentially and composes the results.

### Orchestrator history management

Two mechanisms keep the orchestrator's history from bloating over long sessions:

1. **Delegation exchange compaction (already in the existing spec).** After each `run_skill` returns, the raw tool_call + tool_result pair drops from orchestrator history. The orchestrator's next human-facing assistant message is the only lasting record of what happened.
2. **Aggressive trimming for direct tool calls.** When orchestrator history exceeds its configured character threshold, drop oldest `tool_call` + `tool_result` pairs first. Preserve user↔assistant prose and always preserve the most recent user message.

No LLM-based summarization of orchestrator history in v1. Trimming is purely mechanical.

## Migration

### `agents.yaml`

Entries are evaluated and either dissolved or converted to skills:

| Entry | Disposition |
|-------|-------------|
| `files` | Dissolved — orchestrator uses file tools directly |
| `system` | Dissolved — orchestrator uses `bash` directly |
| `web` | Dissolved — orchestrator uses `web_fetch` directly |
| `email` | Becomes a skill only if procedural content warrants it; otherwise dissolved |
| `scheduler` | Dissolved — orchestrator uses `schedule` directly |
| `memory` | Dissolved — orchestrator reads/writes memory files directly |

`agents.yaml` is removed along with `load_agents_config` and related code.

### Existing skills

Each skill in `skills/` needs audit for small-model fit:

- Skills larger than ~1500 tokens of body must be compressed or split.
- Skills need `tools:` and `max_iterations:` added to frontmatter (these fields do not exist in the current skill format).
- Skills that assume big-model iteration budgets or freeform context need rewriting.

Skills that are unlikely to fit at all (e.g., `deep-research` at ~2000 tokens, `playwright` at ~3000) can be deferred from small-model mode or rewritten as multiple smaller skills.

### `load_skill` tool

Removed. Its name is free; its semantics (load skill text into the current context) are no longer valid in this design. `run_skill` takes its place with different semantics.

## Out of scope (deferred)

- **LLM-based orchestrator history compaction.** Revisit if aggressive mechanical trim loses too much signal. Adds a per-compaction inference call — expensive on small local hardware.
- **Dynamic skill creation at runtime.** The `skill-factory` skill may still exist, but its output is disk-persisted, not injected into the current session's manifest.
- **Sub-agent-to-sub-agent delegation.** Deliberately excluded to bound complexity and prevent unbounded trees.
- **Big-model mode compatibility.** This design is small-model-first. Big-model Curunir retains the existing skill-loading mechanism unchanged.
- **Per-skill model override.** A skill running on a different model than the orchestrator. Possible future extension; not needed for v1.
