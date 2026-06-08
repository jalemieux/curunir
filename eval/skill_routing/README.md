# Skill-Routing & Adherence Evals

Behavioral eval suite for the three reframed live-data skills — **`yfinance`,
`fred`, `polymarket`**. It exists to measure the two things the description
reframe was meant to move:

1. **Routing** — given a use case that *requires* a skill, does the agent
   **load it and call its driver** instead of answering from memory / training
   data? (Graded with `action_used`: the routing contract *is* the action.)
2. **Adherence** — given the intent, does the answer **follow the skill's
   rules**? Cite the freshness stamp (`as_of` / observation date / `fetched_at`),
   use the smallest subcommand (no `info`-by-reflex), report YoY inflation
   rather than the raw CPI index level, frame prediction-market numbers as
   *priced probabilities*. (Graded with `regex_present` / anchored
   `numeric_tolerance` / `llm_judge`.)

> The shared machinery — the graded engine, run flags, report format, statuses,
> the grader catalog, anchoring, and the `Result` contract — is documented once
> in [`eval/README.md`](../README.md). This file covers only what's specific to
> this suite.

## Why these graders are allowed to assert "the skill was used"

The house rule is *grade outcomes, not internals* — but here the routing **is**
the outcome contract. The whole point of the reframe is that a capability-shaped
request ("what's the market-implied probability of …") must reach the live data
path; "it loaded the skill and ran the driver" is the user-visible promise, not
an implementation detail. So `action_used` requiring `load_skill: <skill>` +
the driver (`yfin.py` / `fred.py` / `polymarket.py`) is a legitimate contract,
the same exception the finance suite carves out for routing/privacy.

## Files

| File | Role |
|------|------|
| `skill_routing_tasks.py` | Tasks as data: `{id, name, intent, expected, tags, prompt, max_loops, grader, spec, budget}`. IDs: `YF*` (yfinance), `FR*` (fred), `PM*` (polymarket) |
| `run_skill_routing_evals.py` | Thin shim: builds the `SuiteConfig` and calls `eval.harness.runner.main` |
| `results/` | Timestamped JSON + markdown + HTML reports (git-ignored) |

## Quick start

```bash
# 0. (one-time) activate the venv and set keys in .env
source .venv/bin/activate
#    .env needs:  ANTHROPIC_API_KEY  (the llm_judge for FR3/PM3)
#                 FRED_API_KEY       (fred values + the citation probes)
#                 (yfinance / polymarket need network but no key)

# 1. Terminal A — start the system under test
CURUNIR_PERSONA=finance python run.py

# 2. Terminal B — run the graded suite against it
python eval/skill_routing/run_skill_routing_evals.py              # full suite
python eval/skill_routing/run_skill_routing_evals.py --tag yfinance
python eval/skill_routing/run_skill_routing_evals.py --tag routing
python eval/skill_routing/run_skill_routing_evals.py --id YF2,PM2 # iterate cheap
python eval/skill_routing/run_skill_routing_evals.py --list       # no server needed
```

Any persona whose catalog includes all three skills works; `finance` allowlists
them. **`polymarket` must be un-hidden** for the autonomous-routing probe (PM2)
to be valid — it now is (`skills/polymarket/SKILL.md` no longer sets
`hidden: true`). If you re-hide it, PM2 will fail by design, not by model error.

## The tasks

| id | skill | source | what it catches |
|----|-------|--------|-----------------|
| YF1 | yfinance | regression | bare price routes to `yfin.py` (≤4 actions) |
| YF2 | yfinance | failure-mode | a known fundamental is **fetched + dated**, not recited from memory |
| YF3 | yfinance | failure-mode | smallest subcommand — `quote`, **not** `info`/`financials` by reflex |
| YF4 | yfinance | regression | trailing P/E within 8% of the **live** `yfin.py` value (anchored) |
| YF5 | yfinance | composition | two fetches chain into one ranked comparison (LLY vs NVO) |
| FR1 | fred | regression | treasury-yield lookup routes to `fred.py` |
| FR2 | fred | failure-mode | macro stat fetched **and** cited (series ID + date + %) |
| FR3 | fred | failure-mode | reports **YoY inflation %**, not the raw CPI index level |
| FR4 | fred + yfinance | composition | earnings-yield-vs-treasury seam forces **both** drivers |
| PM1 | polymarket | regression | brand-named request routes to `polymarket.py` |
| PM2 | polymarket | failure-mode | **capability-triggered** routing — auto-routes with the brand *unnamed* (the reframe+un-hide payoff) |
| PM3 | polymarket | failure-mode | cites market URL + implied % + snapshot, framed as a **priced probability** |

## Anchoring

Only **YF4** anchors a live value — its grader re-runs the same
`skills/yfinance/yfin.py multiples NVDA` the agent uses and tolerance-checks
(8%, to absorb intraday drift). FRED and Polymarket values move/require keys, so
those tasks check **citation format and framing** rather than an exact number —
the same choice the finance suite makes for its `fred` citation task.

## Notes & gotchas

- **Routing graders pass without API keys** — `action_used` only checks the call
  was *attempted*. The value-reading checks (YF4's anchor, the citation probes)
  and the judges (FR3/PM3) do need the keys above, in **both** the SUT's env and
  this runner's env.
- **PM2/PM3 prompts don't depend on a specific live market existing** — the
  graders check the routing, the citation *format*, and the *framing*, so they
  stay valid as markets open and resolve.
- The full suite spends real model tokens on the SUT; iterate with `--id` /
  `--tag`. See [`eval/README.md`](../README.md) for the report format and flags.
```
