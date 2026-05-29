# Persona Deployment — Design

**Date:** 2026-05-29
**Status:** Approved (design); pending implementation plan
**Related:** PR #277 (finance agent — reframed as first persona), PR #282 (identity/behavior split), issue #274

## Problem

Curunir today is a single configuration. There is no way to ship a deployment
focused on a domain — a financial assistant, a kids' educator — that bundles a
curated set of skills, a curated set of core tools, the API keys those skills
need, and extra system-prompt content that establishes the specialty
(behavioral guidance, guidelines, guardrails, context).

Every `SKILL.md` auto-registers globally; the tool set is all-or-nothing
(`Agent(tools=None)`); the system prompt is a fixed `identity.md` + `behavior.md`
+ full skill manifest. PR #277 partially addressed this for finance via a
`context.default.finance/` swap behind `CURUNIR_DEFAULTS_DIR`, but that has no
"persona" unit, no skill/tool curation, and replaces identity wholesale rather
than layering on it.

We want a **persona**: a first-class, shippable bundle that drives four
concerns and leaves everything else (model, API base, channels, memory,
scheduling) unchanged.

## What a persona is

A persona is a curated bundle of:

1. **Skills** — an absolute allowlist; only these register for the deployment.
2. **Core tools** — an optional allowlist; omit = all default tools.
3. **API keys** — a deployment/ops concern: documented in the bundle, provided
   by the operator via the container environment. Never declared in code or
   skills.
4. **Specialty prompt** — extra `.md` files layered *on top of* the existing
   `identity.md` + `behavior.md`, bringing domain expertise, focus, and
   guardrails.

## Decisions

- **One persona per deployment**, selected at boot. Not runtime-switchable.
- **Selection** via env var `CURUNIR_PERSONA=<name>`. Unset = exactly today's
  behavior (no regression).
- **Skill allowlist is absolute** — `persona.yaml` is the complete manifest;
  nothing implicit. Forgetting `identity`/`onboarding` is the author's
  responsibility.
- **Tool allowlist is optional** — omit = all default tools. Restricting core
  tools is the rare case and over-restricting breaks `load_skill`/`attach`.
- **Keys stay in the env/runbook layer** — no `requires_keys` in `SKILL.md`, no
  hard boot-time validation. `persona.yaml` may optionally list key *names* for
  a soft startup warning only (never a hard failure).
- **Specialty prompt layers, never replaces** — builds on the #282 split.
- **Persona expertise files bootstrap into `context/`** so the user can edit
  them locally (same lifecycle as `behavior.md` from `context.default/`).
  `persona.yaml` itself stays in the bundle and is read at boot as deployment
  config.

## Architecture

### Selection & backward compatibility

```
  CURUNIR_PERSONA unset  →  EXACTLY today's behavior
                            (all skills in manifest, all default tools,
                             no expertise layer) — zero regression.

  CURUNIR_PERSONA=finance →  resolve personas/finance/ bundle, apply its
                             skill allowlist + tool list + expertise layer.
```

### The bundle (in-repo, shippable)

```
  personas/finance/
    ├── persona.yaml          # the manifest (read at boot — deployment config)
    ├── expertise/            # prompt layers, bootstrapped → context/persona/
    │     ├── 10-domain.md         (capital allocation, thesis lifecycle, tax)
    │     └── 20-guardrails.md     (no regulated advice, cite numbers, defer to user)
    ├── .env.finance.example  # KEY names + comments — the operator's contract
    └── README.md             # runbook: prerequisites, first-boot, keys needed
```

`persona.yaml`:

```yaml
name: finance
description: Local, private personal-finance assistant
skills:            # ABSOLUTE allowlist — only these register
  - identity
  - onboarding
  - financial-analysis
  - investment-memo
  - thesis-management
  - position-tracking
tools:             # OPTIONAL — omit = all default tools
  - read
  - edit
  - write
  - bash
  - load_skill
  - web_fetch
  - attach
  - schedule
keys:              # OPTIONAL — names only, soft startup warning if unset
  - FRED_API_KEY
```

### How each concern wires into existing seams

```
  persona.yaml: skills ──→ AgentConfig.skill_allowlist ──→ load_registry() filters
                tools  ──→ AgentConfig (already drives) ──→ Agent(tools=[...])
                keys   ──→ soft warning at boot (no hard fail, no skill coupling)
  expertise/*.md ──bootstrap──→ context/persona/*.md ──→ build_static_prompt() appends

  Final system prompt order:
    identity.md  +  behavior.md  +  context/persona/*.md (sorted)  +  skill manifest
       (#282)         (#282)            ★ NEW persona layer            ★ NEW filtered
```

### Components / touch points (all small, additive)

| File | Change |
|---|---|
| `src/config.py` | Add `persona: str \| None`, `skill_allowlist: list[str] \| None`, `persona_prompt_dir: Path` (`./context/persona`). |
| `src/skills.py` | `load_registry`/`build_skill_manifest` accept an optional allowlist; filter to it when set. Unset → current behavior. |
| `src/agent/system_prompt.py` | After `behavior.md`, glob `context/persona/*.md` sorted, append each (mirrors the existing optional-file pattern). |
| `onboarding/bootstrap.py` | When `CURUNIR_PERSONA` set, also copy `personas/<name>/expertise/*` → `context/persona/` (non-overwriting). |
| `run.py` | Read `CURUNIR_PERSONA`; parse `persona.yaml` from the bundle; populate config fields; emit the soft key-warning. |

`persona.yaml` is read from the **bundle** at boot (deployment config, like env
vars). Only the **expertise `.md` files** bootstrap into `context/`.

## Reconciling with #277 / #274

#277's finance plan is reframed as the **first persona on this framework**, not
a parallel mechanism:

| #277 as written | Becomes |
|---|---|
| `context.default.finance/` + `CURUNIR_DEFAULTS_DIR` | `personas/finance/` + `CURUNIR_PERSONA` |
| finance `identity.md` (full replace) | `personas/finance/expertise/*.md` (layer on top of base identity+behavior) |
| `.env.finance.example` | moves into the bundle (unchanged in spirit) |
| `thesis-management`, `position-tracking` skills | unchanged — just listed in `persona.yaml` |
| local-Ollama `docker-compose.local.yml` overlay | unchanged — orthogonal to the persona unit |

This design is the generalization; finance validates it.

## Error handling

- **Missing bundle** (`CURUNIR_PERSONA=foo` but no `personas/foo/`) — fail fast
  at boot with a clear message naming the expected path.
- **Malformed `persona.yaml`** — fail fast with the parse error and file path.
- **Missing key** named in `persona.yaml` `keys:` — soft warning to the log,
  boot continues.
- **Skill in allowlist that doesn't exist** — log a warning naming the skill;
  do not crash (the manifest simply won't list it).
- **Missing `context/persona/` dir** (no persona, or empty) — silently skipped
  in `build_static_prompt`, exactly like a missing `behavior.md`.

## Testing

- `test_skills.py` — allowlist filters the registry; unset allowlist = all
  skills (no regression); unknown allowlisted skill warns, doesn't crash.
- `test_system_prompt.py` — persona `.md` files appended in sorted order;
  absent dir silently skipped.
- `test_bootstrap.py` — `CURUNIR_PERSONA` copies expertise into
  `context/persona/`; non-overwriting holds; no persona = no-op.
- Persona-load test — `persona.yaml` parses and drives config; missing bundle
  fails fast with a clear message; malformed yaml fails fast.

## Out of scope (YAGNI)

- Runtime/per-session persona switching.
- Hard key validation or key provisioning.
- `requires_keys` in skill frontmatter.
- A persona registry/discovery UI.
- Per-persona memory schemas beyond what #277 already plans for finance.
```
