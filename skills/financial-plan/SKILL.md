---
name: financial-plan
description: "Use to answer the owner's core retirement / goal-funding question — will my money last? Projects a year-by-year cash flow (accumulate then withdraw), runs a Monte-Carlo probability-of-success against an 85% bar, and stress-tests the plan. Trigger phrases: 'will my money last', 'can I retire at <age>', 'retirement projection', 'am I on track to retire', 'how long will my savings last', 'will I run out of money', 'project my portfolio to age <n>', 'monte carlo my retirement', 'can I afford to retire early', 'stress test my plan', 'what's my probability of success', 'how much can I spend in retirement'. Distinct from balance-sheet (current holdings) and financial-analysis (public companies) — this projects the owner's plan into the future."
tools: financial_plan
portal_summary: "Project whether your money will last — retirement cash flow, Monte Carlo, stress tests"
---

# Financial Plan

Answer *"will my money last?"* with a **deterministic projection engine** — the
same discipline as balance-sheet: **the engine does every calculation. You never
project, compound, or estimate a probability in prose.** A number you reasoned
out yourself instead of running the engine is a bug.

## The model

The engine projects a portfolio through two phases, in **real
(inflation-adjusted) dollars**:

1. **Accumulation** (current age → retirement age): each year adds the annual
   contribution, then grows the balance at the real return.
2. **Distribution** (retirement age → horizon age): each year subtracts annual
   spending, then grows what's left. If the balance goes to zero, that's
   **depletion** — the year the money runs out.

It produces three things:

- **`project`** — the deterministic year-by-year cash-flow table
  (`start_balance`, `+contribution` / `−withdrawal`, `growth`, `end_balance`),
  flagging the depletion age if the base-return path runs dry.
- **`montecarlo`** — draws each year's real return from a (mean, volatility)
  distribution over many seeded paths and reports a **probability of success**:
  the share of futures in which the money lasts to the horizon. Also returns
  ending-balance p10 / p50 / p90 and depletion-age percentiles.
- **`stress`** — re-runs the projection under four canned shocks (below) so you
  see whether the plan survives a bad draw, not just the base case.

## How you reach it

Through the `financial_plan` tool when it's available (call it with an `action`
and an `args` object). Otherwise run the CLI via bash:
`python skills/financial-plan/plan.py <cmd> [flags]`. Both front the same engine.

- **Actions:** `project`, `montecarlo`, `stress`, `report` (markdown of all
  three).
- **`args`:** `{inputs: {...}, seed, n_sims}`. Tool action names are bare words;
  the CLI hyphenates multi-word flags (`--current-age`, `--annual-spending`,
  `--n-sims`, `--from-portfolio`).

**Inputs** (`args.inputs`): `current_age`, `retirement_age`, `end_age`,
`current_balance`, `annual_contribution` (accumulation), `annual_spending`
(distribution). Optional overrides: `real_return`, `volatility`.

## Seed the starting balance from the balance sheet — never invent it

`current_balance` is the owner's real net worth. Get it from the balance-sheet
store, don't guess: run `balance-sheet`'s `networth` (or the CLI
`--from-portfolio`, which reads `portfolio.db` net worth directly). If no
balance sheet exists, **ask the owner** for the starting figure rather than
assuming one.

## The 85% rule

A plan is viable only when its Monte-Carlo **probability of success is ≥ 85%**.
The engine returns `threshold_met`; report it honestly. If a plan comes in
**below 85%, say so plainly** and show what closes the gap (work longer, spend
less, save more) — never round a sub-85% result up into reassurance.

## Stress grid (a plan must survive these, not just the base case)

`stress` re-runs the plan under four shocks. **Judge the plan on these, not the
base case** — a plan that only clears 85% when nothing goes wrong is not a plan:

- **retire 2 years early** — fewer earning years, more drawdown years.
- **−20% year-1 return** — a market drop right at retirement (sequence-of-returns
  risk).
- **+20% spending** — lifestyle creep or under-budgeting.
- **late-life LTC event** — a one-time long-term-care withdrawal late in life.

## Guardrails (non-negotiable)

- **A plan must survive the stress scenarios, not just the base case.** Quote
  the stress-grid success probabilities, and treat a plan that fails under
  stress as a plan that needs changing.
- **Return assumptions must be conservative: real, not nominal, and
  documented.** The engine's defaults are real (inflation-adjusted) and
  deliberately cautious; if you override them, state the number and why, and
  never raise the return to make a failing plan pass.

## Scope

Pre-tax, real-dollar projection only. **Taxes, Social Security, RMDs, and
account-type (pre-tax vs Roth) differences are out of scope** — don't imply the
projection accounts for them. LTC is modeled as a single one-time withdrawal,
not insured/duration-modeled care. Surface these limits when they'd change the
owner's read of the result.
