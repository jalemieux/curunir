# Default-Persona Evals

Behavioral eval suite for `CURUNIR_PERSONA=default` (or unset — default is the
fallback). Because the default persona carries the **full skill catalog with no
allowlist**, this suite doubles as the **model/quant-swap smoke test**: it
probes the framework-level behaviors every persona rests on, plus routing
across the widest (hence most collision-prone) catalog.

> The shared machinery — the graded engine, run flags, report format, statuses,
> the grader catalog, anchoring, and the `Result` contract — is documented once
> in [`eval/README.md`](../README.md). This file covers only what's specific to
> this suite.

## Files

| File | Role |
|------|------|
| `default_tasks.py` | Tasks as data (see the family map below) |
| `run_default_evals.py` | Thin shim: builds the `SuiteConfig` and calls `eval.harness.runner.main` |
| `_schedules.py` | Anchor/cleanup helper for K1: reads and deletes rows in `context/schedules.db` via the same `schedule_store.engine` the agent uses |
| `fixtures/memory/baseline/` | Seeded memory for the fixture-tagged tasks (K2's dentist fact, K3's delegation passphrase) |
| `results/` | Timestamped JSON + markdown + HTML reports (git-ignored) |

## The task families

| family | ids | what it owns |
|--------|-----|--------------|
| **G** | G1–G3 | the no-general-knowledge guardrail (#338): ground external facts in a tool result (G1), honor the explicit-recall exception (G2), don't over-trigger on meta turns (G3) |
| **S** | S1–S8 | skill *discovery* (#451/#457): reach catalog skills by name via `load_skill`, never filesystem-hunt for `SKILL.md`; includes over-trigger and grader-integrity guards |
| **K** | K1–K6 | framework-kernel tripwires: scheduling persists to `schedules.db` (anchored), memory recall, delegate handoff, attachments, slash dispatch, multi-turn retention |
| **RS** | RS1–RS13 | routing sweep: one canonical trigger prompt per visible catalog skill not covered elsewhere, graded on `load_skill: <name>` only |
| **WS/RR** | WS1, WS5, RR1, RR5 | migrated skill-routing tripwires (from `eval/skills/web_search` and `eval/skills/reddit_research`): richer routing contracts — budgets, blocked-site forbid-lists, capability-triggered prompts — for the two skills whose *method* suites live under `eval/skills/` |

Routing lives here rather than in the skill suites because a routing contract
is a property of the **persona's catalog** (collision/shadowing), not of the
skill — see the taxonomy in [`eval/README.md`](../README.md). The migrated
tasks keep their original ids for result-history continuity.

## Quick start

```bash
# Terminal A — the system under test
CURUNIR_PERSONA=default python run.py

# Terminal B — the graded suite
python eval/default/run_default_evals.py --fixture baseline   # everything (K2/K3 need the fixture)
python eval/default/run_default_evals.py --tag routing        # just the routing tripwires
python eval/default/run_default_evals.py --tag routing-sweep  # just the RS sweep
python eval/default/run_default_evals.py --id G1,S3           # iterate cheap
python eval/default/run_default_evals.py --list               # no server needed
```

For a cross-model sweep: `SUITE=eval/default/run_default_evals.py
EVAL_ARGS="--fixture baseline" eval/model_sweep.sh`.

**Keys.** The `action_used` tasks (S, K1/K4/K5, RS, WS/RR) pass without API
keys — they check the call was *attempted*. The `llm_judge` tasks (G-family,
S6) need `JUDGE_MODEL`/`MODEL` + a key in the runner's env; the WS/RR prompts
need `BRAVE_API_KEY` + network in the SUT's env to produce grounded answers.
