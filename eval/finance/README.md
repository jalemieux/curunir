# Finance-Persona Evals

Behavioral eval suite for `CURUNIR_PERSONA=finance`. Tests **end-to-end
behavior at the boundary** — real user prompts in, graded pass/fail out —
never internals like "did it call skill X" (except where a routing/privacy
*contract* genuinely is the action taken).

> The shared machinery — the graded engine, run flags, report format, statuses,
> the grader catalog, anchoring, and the `Result` contract — is documented once
> in [`eval/README.md`](../README.md). This file covers only what's specific to
> the finance suite.

## Files

| File | Role |
|------|------|
| `finance_tasks.py` | Tasks as data: `{id, name, tags, prompt`/`prompts, max_loops, grader, spec, budget}`. R/F/C = market data; **P/T/W = position-tracking** (the owner's balance sheet) |
| `run_finance_evals.py` | Thin shim: builds the finance `SuiteConfig` and calls `eval.harness.runner.main` |
| `_pe_gap.py` | Anchor helper for C1 (live forward-P/E gap) |
| `_networth.py` | Anchor helper for the T family: queries the seeded SQLite store's shared `v_networth` / `v_rollup_by_class` / `v_collectibles_pnl` views for `total` / `rollup` / `re-equity` / `collectibles` |
| `fixtures/portfolio.sql` | Frozen, synthetic-but-faithful portfolio seed (tables + the shared views). Seed `context/memory/portfolio.db` from it before any tracking task (see [Bootstrap the portfolio store](#bootstrap-the-portfolio-store-one-time-before-p2tw)); the agent and the anchor both read that store |
| `fixtures/memory/baseline/` | The free-form memory representation of those same holdings, seeded into `context/memory/` for a tracking run |
| `results/` | Timestamped JSON + markdown + HTML reports (git-ignored) |

## Quick start

```bash
# 0. (one-time) activate the venv and set keys in .env
source .venv/bin/activate
#    .env must have:  ANTHROPIC_API_KEY  (the Claude judge)
#                     FRED_API_KEY / BRAVE_API_KEY / XAI_API_KEY / GEMINI_API_KEY
#                     (the finance data + research skills — see personas/finance/README.md)

# 1. Terminal A — start the system under test with the finance persona
CURUNIR_PERSONA=finance python run.py

# 2. Terminal B — run the graded suite against it
python eval/finance/run_finance_evals.py            # full suite
python eval/finance/run_finance_evals.py --id R6,F9 # a subset while iterating
python eval/finance/run_finance_evals.py --tag regression
```

See [`eval/README.md`](../README.md) for the full flag list, the report format,
and the judge-model setup. The full suite spends real model tokens on the SUT
(the F11 memo alone runs ~8–10 min end to end), so iterate with `--id` / `--tag`.

## Position-tracking tasks (the P/T/W families)

The `tracking`-tagged tasks (P2, T1–T5, W1–W4) ask about the *owner's* stored
state — the balance sheet for P2/T*/W1–W3, the idea log (`idea-log.md`, #506)
for W4 — so they must read a **seeded memory fixture** rather than being handed
the facts in-prompt; that's the only way they measure the memory-schema and
memory-convention behavior. `--fixture <name>` stashes the real `context/memory/`, seeds
`fixtures/memory/<name>/` in, runs, and **restores on exit** (even on error):

```bash
# Baseline (free-form memory): expect many T/W reds — that is the point.
python eval/finance/run_finance_evals.py --tag tracking --fixture baseline
python eval/finance/run_finance_evals.py --id P1            # P1 is stateless; run it separately
```

`--fixture` is **local-SUT only** — it touches this machine's filesystem and
refuses if `--host` is non-local. The fixture values are **frozen** (a
balance-sheet benchmark must be reproducible); the agent is expected to use the
stored values, not re-fetch live. The W family is **multi-turn** (`prompts: […]`
sent over one session — a write turn, then a readback turn the grader scores),
replaying the incremental-addition scenario that caused the real drift.

The truth behind the T-family graders comes from `_networth.py`, which queries
the **same `v_networth` / `v_rollup_by_class` / `v_collectibles_pnl` views the
agent's portfolio engine exposes**, against the seeded SQLite store. Both the
agent and the grader's anchor read this one store, so it must be seeded from the
frozen `fixtures/portfolio.sql` **before** running any tracking task.

### Bootstrap the portfolio store (one-time, before P2/T*/W*)

The fixture is a plain SQL seed (tables + views + the frozen rows). There is no
make-target — build the store with `sqlite3`. The fixture's `CREATE TABLE`
statements have no `IF NOT EXISTS`, so they collide with the bare schema
`init_db()` leaves behind; **build a fresh file**, don't load onto an existing
one:

```bash
# Default path — both the SUT and the anchor read this by default.
rm -f context/memory/portfolio.db
sqlite3 context/memory/portfolio.db < eval/finance/fixtures/portfolio.sql
```

Then verify the anchors are non-zero (an empty store reports `net_worth: 0` — the
tell-tale sign the seed never ran):

```bash
python eval/finance/_networth.py total      # {net_worth: 4135169, assets: 5690169, liabilities: 1555000}
python eval/finance/_networth.py rollup     # {equities, real_estate_equity, …, total}
```

**`--fixture baseline` caveat.** That run stashes *all* of `context/memory/`
aside (see above), which also hides a `portfolio.db` sitting there — the anchor
would then read an absent store. For baseline runs, seed the store **outside**
`context/memory/` and point both shells at it via `CURUNIR_PORTFOLIO_DB` (which
both `_networth.py` and the anchor subprocess honor) so it survives the stash:

```bash
sqlite3 eval/finance/portfolio.db < eval/finance/fixtures/portfolio.sql
export CURUNIR_PORTFOLIO_DB=$PWD/eval/finance/portfolio.db   # set in BOTH terminals (SUT + runner)
```

### Two interface modes (CLI vs tool)

Once the A/B/D portfolio engine ships, the same balance sheet is reachable two
ways — a **CLI adapter** and an **opt-in tool adapter** — and this suite is the
judge of which surface wins. The tasks grade at the boundary (correct net worth
in `final_text`), so they don't change between modes; only the SUT's
configuration does. Run the suite once per surface and tag each run so the
reports diff cleanly:

```bash
# (configure the SUT for the CLI surface, then)
python eval/finance/run_finance_evals.py --tag tracking --fixture baseline --interface cli
# (reconfigure the SUT for the tool surface, then)
python eval/finance/run_finance_evals.py --tag tracking --fixture baseline --interface tool
```

`--interface` only labels the report (filename + header); the winner is the
surface with the better pass-rate and tool-call efficiency.

## The task families — the four sources applied to finance

(The general methodology is in [`eval/README.md`](../README.md#how-a-suite-is-built--the-four-sources); here is how it maps onto the finance suite.)

1. **Regression tripwires** (`R1`–`R7`) — one deliberately easy task per core
   capability (each data CLI, web search, the financial-analysis and
   investment-memo orchestrators).
2. **Failure-mode probes** (`F1`–`F11`) — one prompt per known pathology of
   *this* design:
   - **mis-route** — `F1` (a recommendation must hit `investment-memo`, not
     `deep-research`), `F2` (an event seed must hit `catalyst-memo`).
   - **guardrails** — `F3` no regulated advice, `F4` never execute/simulate a
     trade, `F5` never leak private holdings to a third party.
   - **hallucination** — `F6` flag future/stale data instead of inventing,
     `F7` fetch fundamentals instead of reciting (cap must match live).
   - **dropped work** — `F8` show arithmetic + citations, `F10` surface a
     thesis's named disconfirming evidence, `F11` keep the Fact-Check Addendum.
   - **over-orchestration** — `F9` a trivial lookup must not spin up a memo.
3. **Composition points** (`C1`–`C4`) — two-ticker comparable (`C1`), catalyst →
   winners/losers + odds (`C2`), analysis pulling a real FRED discount rate
   (`C3`), position-tracking ⋈ tax-timing (`C4`).
4. **Position-tracking** (`P*`/`T*`/`W*`) — the owner's balance sheet; see above.

### Anchors

The finance suite's live-data anchors (see [Anchoring](../README.md#anchoring-no-hardcoded-mutable-answers)):

- `R2` trailing P/E, `F7` market cap → `yfinance/yfin.py multiples`
- `R4` CIK (frozen, exact) → `sec-edgar/edgar.py lookup`
- `C1` forward-P/E gap → `_pe_gap.py`
- `T*` net worth / rollup → `_networth.py` (the seeded portfolio store)

## A note on baselines

A failure is the agent's, not the harness's — the captured `final_text` and
`actions` show exactly what the model did. (Baseline on
`openrouter/z-ai/glm-5-turbo`: 19 pass, 2 fail, 1 slow — F3 gave a bare buy/sell
directive under "don't hedge" pressure; F2 didn't route an event seed to
catalyst-memo; F11 produced a correct memo but over the 10-min budget.)

## Adding a finance task

1. Write the **grader first** — if you can't state a discriminating pass/fail
   check, the prompt is too vague; sharpen it.
2. Add the task dict to `finance_tasks.TASKS` with source + symptom **tags**.
3. If the answer can change with data, add an `anchor` instead of a constant.
4. If *how* it succeeds matters (speed/cost), add a `budget`.
