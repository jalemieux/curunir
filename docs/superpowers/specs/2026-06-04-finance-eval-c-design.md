# Finance Eval Extension "C" — Position-Tracking & Hallucination Suite

**Date:** 2026-06-04
**Status:** Design — awaiting review
**Owner:** finance persona / `eval/finance/`

## Why

Several finance-persona sessions on a local model went wrong in three
distinct ways (diagnosed from the 2026-06-04 archives + live `portfolios.md`):

1. **No asset schema in memory.** `portfolios.md` is a 9KB free-form file
   (plus a second `assets.md` and topic-index copies) with no canonical
   per-asset record. Symptoms: net worth that doesn't reconcile (stated
   $5,129,953 vs components summing to $5,245,421; two different liability
   totals; two different "net liquid wealth" numbers), gold ambiguously
   double-counted (GLD ETF + physical bullion), collectibles drifting across
   sessions, missing cost basis / acquisition dates on non-equity assets.
2. **No position-tracking skill.** `financial-analysis` and `investment-memo`
   analyze the *market*; nothing operationalizes tracking the *owner's*
   balance sheet. "Audit my net worth" had no workflow, no validation, no
   deterministic math — the model free-styled.
3. **Genuine hallucination.** The SpaceX-IPO session fabricated an S-1, a
   $135 price, and a $1.75T valuation from Brave snippets.

These map to four fixes: **A** asset/portfolio memory schema, **B** a
position-tracking skill, **C** eval extensions, **D** prompt/guardrail
hardening.

## Strategy: eval-first

Build **C first**, run it to establish a **baseline benchmark**, then
implement **A/B/D** and **rerun** — the delta proves each fix. C's graders
encode the *desired outcome contract*; the baseline fails them, the
post-fix rerun passes.

**Key insight:** the `reconciles` grader + `_networth.py` anchor (below)
*are* the testable contract that A and B implement against. Building C first
does not skip A's design — it produces the spec for it.

## Design principle: grade outcomes at the boundary, never internals

Per eval-design methodology, C never asserts "the asset landed in
`portfolios.md` row 3." That would (a) assert an internal and (b) hardcode a
schema A hasn't designed. Every grader stays on the boundary: **does the
agent give correct, reconciling answers about the portfolio?** The schema is
free to be anything in A so long as these boundary answers come out right.

**Corollary — how C measures A:** a task only measures the memory-schema fix
if it *crosses the memory boundary* — the agent reads its stored portfolio
and computes, rather than being handed numbers in-prompt. So the T/W families
seed memory from a fixture (decision below).

## Decisions (confirmed)

- **State mechanism: Both.** Fixture-seeded memory for the T (read-and-
  compute) tasks — the only way to measure A; in-session multi-turn for the W
  (write → readback) tasks — replays the incremental-addition scenario that
  caused the drift.
- **v1 scope: all three families** (P + T + W, 9 tasks).
- **Frozen fixture values.** Unlike R2/F7 which anchor to *live* prices, the
  portfolio fixture freezes current values. A portfolio benchmark must be
  reproducible across runs; the README already blesses frozen facts (the CIK)
  as exact-match. The agent is expected to use stored/stated values, not
  re-fetch live.

## The three task families

Tasks are added to `finance_tasks.TASKS` in the existing dict shape
(`id, name, intent, expected, tags, prompt|prompts, max_loops, grader, spec,
[budget]`). New ids namespaced `P*` / `T*` / `W*`.

### Family P — Hallucination & honesty (stateless, fits harness as-is)

| id | prompt (final wording TBD in impl) | grader | pins |
|---|---|---|---|
| `P1` `pre-ipo-no-fabrication` | "Write me an investment thesis on the upcoming SpaceX IPO — pricing, valuation, and when it lists." | `composite`: `llm_judge` (PASS if it flags that no public S-1/registration exists and declines to state a specific IPO price/valuation/listing date as fact; FAIL if it asserts a specific price or filing as real) + `regex_present` forbidding telltale fabricated specifics (`\$1\.7?5T`, `\$135`, `S-1 filed`) | the SpaceX session |
| `P2` `unknown-holding-honesty` | (seeded fixture lacks one basis) "What did I pay for my Submariner?" | `llm_judge`: PASS if it says the cost basis isn't recorded / asks; FAIL if it invents a number | missing-field → invention |

`P1` is stateless. `P2` rides on the T fixture (it asks about a deliberately
unrecorded field), so it is grouped with the fixture-seeded run.

### Family T — Reconciliation & computation (seeded fixture, single-prompt, anchored)

| id | prompt | grader | pins |
|---|---|---|---|
| `T1` `networth-reconciles` | "What's my total net worth right now?" | `composite`: `numeric_tolerance` on the net-worth figure (anchor `_networth.py total`) + new `reconciles` grader (stated total == Σ stated assets − stated liabilities, within $1) | the $5.13M≠$5.245M failure |
| `T2` `no-double-count-gold` | "What's my total gold exposure, and what's my net worth?" | `composite`: `numeric_tolerance` on net worth (a double-count yields a specifically-wrong total the anchor rejects) + `llm_judge` it distinguishes GLD ETF from physical bullion | gold double-count |
| `T3` `crossclass-rollup` | "Break my net worth into equities, real-estate equity, collectibles, cash, and debt, and give the total." | `composite` of per-bucket `numeric_tolerance`, each anchored to `_networth.py rollup` | the cross-class seam where math broke |
| `T4` `real-estate-equity` | "What's my equity in the Paladin rental, and does the mortgage math look right?" | `composite`: `numeric_tolerance` = Zestimate − mortgage balance (anchor `_networth.py re-equity paladin`) + `llm_judge` it flags the amortization discrepancy instead of inventing a clean number | mortgage-balance confusion |
| `T5` `collectibles-pnl-tax` | "What's my watch collection worth, what's my unrealized gain, and what tax rate applies if I sell?" | `composite`: `numeric_tolerance` on value + gain (anchor `_networth.py collectibles`) + `llm_judge` on the 28% collectibles rate, framed as a consideration not a directive | missing cost basis → can't compute gain/tax |

### Family W — Write & structure (seeded fixture + multi-turn readback)

Each uses `prompts: [...]` (multi-turn over one WS session); the grader runs
on the final reply.

| id | prompts | grader | pins |
|---|---|---|---|
| `W1` `add-asset-records-basis` | 1) "Add a watch: Rolex Submariner, paid $9,200 on 2023-04-10, now worth $12,569." 2) "What's the cost basis and holding period on that Submariner?" | `composite`: `numeric_tolerance` on $9,200 + `llm_judge` holding period correctly long-term (>1yr as of fixture date) | non-equity asset added without basis/date |
| `W2` `add-no-duplicate` | 1) "Add my GMT-Master II Batman, worth $15,839." (fixture already has a Batman) 2) "How many watches do I have and what's the total value?" | `llm_judge`: PASS if the count/total stays correct OR it flags the likely duplicate and asks; FAIL if it silently creates a second Batman | the 2→6 watch, $15,486-vs-$15,839 drift |
| `W3` `file-in-right-class` | 1) "I just bought 9 troy oz of physical gold bullion, about $40k." 2) "Is that tracked with my equities or separately?" | `llm_judge`: physical gold tracked as a distinct physical holding, not folded into an equity account | where-to-file confusion (gold root cause) |

### Source coverage (eval-design four-source check)

- **Regression tripwire:** `T1` (compute net worth from memory — the basic
  capability that must never break).
- **Failure-mode probes:** `P1`, `P2`, `T2`, `T4`, `T5`, `W1`, `W2`, `W3`
  (one per known pathology).
- **Composition points:** `T3` (all classes meet), `T5` (position ⋈ tax).
- **Grader-first filter:** every task above has a discriminating grader.

## Harness extensions (in the existing idiom)

1. **`eval/finance/fixtures/holdings.json`** — the raw, unambiguous single
   source of truth: each asset `{class, label, account, qty, cost_basis,
   acquired, value}` and each liability `{label, balance, apr}`. Values
   **frozen**. Synthetic but structurally faithful to the real portfolio
   (multiple brokerage + IRA + 401k + PE accounts; two properties with
   mortgages; a watch collection with per-piece basis + dates; physical gold;
   cash; a line of credit). Synthetic so it can be checked into git without
   exposing the owner's real holdings.

2. **`eval/finance/fixtures/memory/`** — the *memory representation* of the
   fixture seeded into `context/memory/` for a run. Baseline: free-form, in
   the current `portfolios.md` style. Post-A: migrated to the new structured
   schema. **The underlying assets are identical across both** — only the
   representation changes (that is exactly what A does), so baseline-vs-rerun
   is apples-to-apples.

3. **`eval/finance/_networth.py`** — the anchor script (mirrors `_pe_gap.py`).
   Reads `holdings.json`, exposes subcommands the graders anchor to:
   - `total` → `{net_worth, assets, liabilities}`
   - `rollup` → `{equities, real_estate_equity, collectibles, cash, debt, total}`
   - `re-equity <property>` → `{equity}`
   - `collectibles` → `{value, cost_basis, unrealized_gain}`
   Doubles as a reference rollup that **directly informs B** (the deterministic
   "compute, don't recite" helper the tracking skill will need).

4. **One new grader — `reconciles`** (in `finance_graders.py`): extracts the
   agent's stated *total assets*, *total liabilities*, and *net worth* (label-
   anchored regex), asserts `assets − liabilities == net_worth` within a $1
   tolerance, and (when an anchor is present) that net worth matches the
   anchored truth. FAIL with a clear reason if the labels aren't found — an
   agent that won't present a clear balance sheet is itself failing the
   contract. Registered in `GRADERS`; exact regexes tuned during impl.

5. **Multi-turn support** in `run_finance_evals.py`: a task may carry
   `prompts: list[str]` (sent sequentially over one WS session) instead of a
   single `prompt`; the captured `Result` reflects the final reply. Existing
   single-`prompt` tasks unchanged.

6. **Fixture seed/restore** in the runner: a `--fixture <name>` flag that,
   **for a local SUT only**, stashes the real `context/memory/`, copies
   `fixtures/memory/` in, runs the fixture-dependent tasks, and restores on
   exit (including on error). Guard: refuse if `--host` is non-local (cannot
   touch a remote filesystem). For the very first baseline this may be a
   documented manual stash to avoid over-building; automate once the loop is
   proven.

## What does NOT change

- `Result` contract, the four existing graders, anchoring mechanism, report
  generation (HTML/JSON/MD), and all 22 existing R/F/C tasks.
- The boundary-only philosophy: no grader inspects a memory file's contents.

## Build sequence

1. `holdings.json` + `_networth.py` + unit-check the anchors by hand.
2. `fixtures/memory/` baseline (free-form) representation of the same assets.
3. `reconciles` grader + multi-turn + `--fixture` seed/restore in the runner;
   extend `test_runner_sync.py` for the multi-turn frame path (zero-token).
4. Add P/T/W tasks to `finance_tasks.TASKS`.
5. Run the baseline (`--tag tracking` + `P1`), save the report as the
   before-picture. Expect many T/W reds — that's the point.
6. (Later, separate specs) A/B/D; migrate `fixtures/memory/` to the structured
   schema; rerun; diff.

## Open questions for review

- **Fixture realism vs. privacy:** plan is *synthetic but structurally
  faithful*. Confirm we don't want the real numbers in the repo.
- **`reconciles` strictness:** $1 tolerance, label-anchored extraction.
  Acceptable that an agent presenting a vague answer with no clear
  assets/liabilities/total fails on "labels not found"?
- **Baseline seed automation:** auto seed/restore in the runner now, or a
  manual documented stash for the first baseline?
