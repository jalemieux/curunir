# Prompt-owned delivery; sub-agents are workers (#411)

**Date:** 2026-06-21
**Issue:** [#411](https://github.com/jalemieux/curunir/issues/411) — Sub-agent can deliver an un-fact-checked PDF, bypassing the catalyst/investment-memo fact-check gate
**Supersedes:** the approach in PR [#418](https://github.com/jalemieux/curunir/pull/418) (tool-layer denylist only)

## Problem

Only the agent interfacing with the user should be able to deliver an artifact
(an `attach`/`to_audio` egress) to that user. A `delegate`-spawned sub-agent is
a background worker: it should return a text digest, and the orchestrator owns
delivery and the fact-check gate.

Today that boundary leaks because **the sub-agent receives the identical system
prompt as the main agent**:

- `build_static_prompt(config)` is config-only — no sub-agent awareness. So a
  worker gets every "deliver the artifact" instruction the main agent gets.
- The delivery instruction lives in **`personas/default/prompts/behavior.md`**
  (the "Deliverables" section). It is *not* shared cross-persona — finance and
  marketing don't have it, so for those personas the only "attach" instruction
  comes from skills.
- Skills both **declare** `tools: attach` (frontmatter) and **instruct**
  `attach(...)` (prose). The frontmatter, via the `load_skill` opt-in-unlock
  path, can grant `attach` to a sub-agent that loads the skill.

Net: a worker is told to deliver, lacks the tool, loads a skill to acquire it,
and self-delivers an un-fact-checked artifact.

The current PR patches only the capability (an `is_sub_agent`-gated unlock
denylist). It leaves the *instruction* in place, so the model keeps hunting for
an egress path. The root fix is to stop telling a worker to deliver, and to make
the delivery instruction a single cross-persona thing the main agent owns.

## Design

Delivery is a property of **which agent you are**, stated once in prompt
assembly and never duplicated in skills.

```
                       BEFORE                          AFTER
delivery instruction   default persona behavior.md     shared snippet in
                       + per-skill "attach the PDF"     build_static_prompt (all personas)

main agent prompt      = identity+persona+manifest      = ...+ DELIVERY snippet
sub-agent prompt       = SAME (the bug)                  = ...− DELIVERY snippet (+ worker note)

skills                 tools: attach / "attach(...)"     no attach mention at all

capability (backstop)  is_sub_agent unlock denylist      unchanged — guarantees
                                                          a sub-agent never gets attach/to_audio
```

### 1. Shared, cross-persona delivery snippet

Extract the attach/PDF delivery instruction (currently
`personas/default/prompts/behavior.md` lines ~34-36) into a module-level
constant `_DELIVERY_SNIPPET` in `src/agent/system_prompt.py`. It becomes a
common part of prompt assembly injected for **every** persona — finance and
marketing gain it (today they lack it).

Canonical wording carried by the snippet (preserving today's behavior):

- Substantial documents (report, analysis, memo) are delivered as a file, via
  `attach`, by default — don't wait to be asked.
- Write Markdown → convert to **PDF** with `pandoc` → attach the PDF; PDF is the
  default attachment format, Markdown renders inline. If pandoc fails, attach
  the `.md` as fallback. No HTML/Chromium/CSS rendering. Never `pip install` at
  runtime.

The **Workspace** section of `behavior.md` (writable paths, scratch vs
generated) is *not* part of the snippet — it stays shared/unconditional because
sub-agents still write files; they just don't deliver. Remove the extracted
delivery lines from `behavior.md` so the snippet is the single source of truth.

### 2. `build_static_prompt` becomes sub-agent-aware

```python
def build_static_prompt(config: AgentConfig, is_sub_agent: bool = False) -> str:
    ...
    if is_sub_agent:
        parts.append(_SUBAGENT_WORKER_NOTE)
    else:
        parts.append(_DELIVERY_SNIPPET)
    return "\n\n".join(parts)
```

- `_DELIVERY_SNIPPET` — appended only for the main agent.
- `_SUBAGENT_WORKER_NOTE` — appended only for sub-agents: *"You are a background
  worker spawned to complete a single task. Return your result as a text digest
  — you cannot deliver anything to the user (no attachments, no audio). The
  agent that delegated to you owns delivery and the fact-check gate. Do not try
  to attach, email, or otherwise send an artifact; just return the content."*

`Agent.__init__` passes `self.is_sub_agent` (already present from PR #418) into
`build_static_prompt`. No other call sites change — `delegate.py` already
constructs the sub-agent `Agent` with `is_sub_agent=True`.

### 3. Skills stop calling for attach

Skills describe *what to produce*; the system prompt owns *how it is delivered*.
For each skill that currently references `attach`:

- Remove the `tools: attach` frontmatter line.
- Remove/rewrite prose that instructs `attach(...)` and the "Forgetting
  `attach()`" common-mistake bullets. Keep the *content* guidance (what the
  report contains, where to write it, what to post inline) — drop only the
  delivery mechanics, which the shared snippet now covers.
- Delete `financial-analysis`'s per-skill "When running as a sub-agent…" block
  — the worker note in the prompt makes it generic and redundant.

Files to edit (all under `skills/`):

- `financial-analysis/SKILL.md` — frontmatter + Step 5 + sub-agent block
- `investment-memo/SKILL.md` — frontmatter + deliver step + mistake bullet
- `catalyst-memo/SKILL.md` — frontmatter + deliver step + mistake bullet
- `deep-research/SKILL.md` — frontmatter + deliver step + mistake bullet
- `deep-research-guided/SKILL.md` — frontmatter + deliver step + mistake bullet
- `superheroes/SKILL.md` — frontmatter only
- `skill-factory/SKILL.md` + `references/template.md` — update the authoring
  docs: `tools:` is for genuine **opt-in** tools (`portfolio`, `to_audio`), not
  `attach` (which is a base tool the main agent always has and must never be
  declared by a skill).

### 4. Capability backstop — unchanged

Keep PR #418's unlock-path enforcement (`_SUBAGENT_BLOCKED_UNLOCKS =
{"attach", "to_audio"}`, gated on `is_sub_agent`). After change #3 no skill
declares `attach`, so the leak is closed at the source — but the backstop
guarantees the invariant *"a sub-agent never holds a delivery tool"* even if a
future skill re-declares one. `attach` is not in `_SUB_AGENT_TOOLS`, so a
sub-agent has no delivery capability by construction; the backstop only governs
the unlock path. `to_audio` stays opt-in and unlockable by the **main** agent
via `conversation-to-audio`; the backstop blocks that same unlock for a worker.

## Components & boundaries

| Unit | Responsibility | Depends on |
|---|---|---|
| `_DELIVERY_SNIPPET` (const) | The one place that says "deliver artifacts via attach/PDF" | — |
| `_SUBAGENT_WORKER_NOTE` (const) | The one place that tells a worker "return a digest, don't deliver" | — |
| `build_static_prompt(config, is_sub_agent)` | Assemble prompt; choose snippet vs worker note | the two constants |
| `Agent.__init__` | Pass `is_sub_agent` into assembly | `build_static_prompt` |
| `_SUBAGENT_BLOCKED_UNLOCKS` (unchanged) | Capability backstop on the unlock path | — |
| skills | Describe content only; never delivery | the shared snippet (implicitly) |

## Testing

- `build_static_prompt(config, is_sub_agent=False)` contains the delivery
  snippet and not the worker note; `is_sub_agent=True` is the inverse.
- The delivery snippet is present for **all** personas (default, finance,
  marketing) — a regression test that the finance persona prompt now includes
  it (it didn't before).
- A sub-agent that loads a skill formerly declaring `tools: attach` does not
  gain `attach` (existing PR #418 test still passes).
- Grep guard: no `SKILL.md` declares `tools: attach`.
- `behavior.md` no longer contains the delivery lines (they moved to the
  snippet) but still contains the Workspace section.

## Out of scope

- Reworking the `tools:` opt-in mechanism itself (portfolio/to_audio unlock
  stays as-is for the main agent).
- A data-driven `unlockable_tools` set on `Agent` (considered; not needed once
  skills don't declare `attach` and the existing backstop holds).
- Allowing sub-agents any egress path (they remain digest-only by design).
