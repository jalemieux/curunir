# Finance-Persona Evals

Behavioral eval suite for `CURUNIR_PERSONA=finance`. Tests **end-to-end
behavior at the boundary** — real user prompts in, graded pass/fail out —
never internals like "did it call skill X" (except where a routing/privacy
*contract* genuinely is the action taken).

## Files

| File | Role |
|------|------|
| `finance_tasks.py` | 22 tasks as data: `{id, name, tags, prompt, max_loops, grader, spec, budget}` |
| `finance_graders.py` | Pure graders `(result, spec) -> (status, why)`; `GRADERS` dispatch + `grade()` |
| `run_finance_evals.py` | Runner: drives the WS channel, builds a `Result`, grades, writes a report |
| `_pe_gap.py` | Anchor helper for C1 (live forward-P/E gap) |
| `test_runner_sync.py` | Zero-token tests for the runner's WS frame handling (no SUT needed) |
| `results/` | Timestamped JSON + markdown reports (git-ignored) |

## Quick start

The runner is a **headless WebSocket client** — it talks to a running curunir
instance over `ws://localhost:8765`, the same channel the CLI uses. So you need
the server running first, then run the suite against it from another shell.

```bash
# 0. (one-time) activate the venv and make sure keys are set in .env
source .venv/bin/activate
#    .env must have:  ANTHROPIC_API_KEY  (the Claude judge)
#                     FRED_API_KEY / BRAVE_API_KEY / XAI_API_KEY / GEMINI_API_KEY
#                     (the finance data + research skills — see personas/finance/README.md)

# 1. Terminal A — start the system under test with the finance persona
CURUNIR_PERSONA=finance python run.py

# 2. Terminal B — run the graded suite against it
python eval/finance/run_finance_evals.py
```

That prints a live status line per task and, at the end, a summary plus the path
to a saved report under `eval/finance/results/`.

### Run a subset (cheaper)

```bash
python eval/finance/run_finance_evals.py --id R6,F9      # just these task ids
python eval/finance/run_finance_evals.py --tag regression # tasks whose tag matches
python eval/finance/run_finance_evals.py --tag guardrail  # any tag regex works
python eval/finance/run_finance_evals.py --no-grade       # capture only, skip grading
python eval/finance/run_finance_evals.py -v               # stream tool calls + text live
python eval/finance/run_finance_evals.py --host h --port 8765   # remote SUT
```

`-v`/`--verbose` prints each task's tool calls (`├─ load_skill: …`,
`├─ bash: …`) and streamed text as it happens, then the grade — useful for
watching *why* a task is heading toward pass or fail in real time.

The full suite spends real model tokens on the SUT (the F11 memo alone runs
~8–10 min end to end). Use `--id` / `--tag` while iterating; run the full 22
only when you want a complete baseline.

### Verify the runner itself (no server, no tokens)

The runner's WS frame handling is covered by a fake-socket test that replays the
exact server frame sequence — run it after touching `run_finance_evals.py`:

```bash
python eval/finance/test_runner_sync.py
```

### The judge model

`llm_judge` tasks are graded by a **Claude model, separate from the system under
test** (a model grading its own output is an eval anti-pattern). The runner
loads `.env`, so the default `anthropic/claude-sonnet-4-6` works as long as
`ANTHROPIC_API_KEY` is set. Override with `JUDGE_MODEL=<litellm-model-id>` (and
that provider's key in the env).

## Reading the output

Each task prints `PASS` / `FAIL` / `SLOW` / `ERR` and a one-line reason. The
saved report comes in two forms:

- `results/finance-<ts>-<model>.md` — a `## Summary` table
  (`id | name | status | why`) **followed by a `## Trace` section**: for every
  task, the full prompt, each tool call, attachments, the agent's complete final
  text, and per-task stats. A failure can be diagnosed from this file alone.
- `results/finance-<ts>-<model>.json` — the same capture as structured data
  (`final_text`, `actions`, `attachments`, `wall_ms`, `turns`, `tokens_out`),
  for programmatic diffing across runs.

For a live trace while it runs, add `-v` (see Quick start).

A failure is the agent's, not the harness's — the captured `final_text` and
`actions` show exactly what the model did. (Baseline on
`openrouter/z-ai/glm-5-turbo`: 19 pass, 2 fail, 1 slow — F3 gave a bare buy/sell
directive under "don't hedge" pressure; F2 didn't route an event seed to
catalyst-memo; F11 produced a correct memo but over the 10-min budget.)

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `received 1008 (policy violation) auth` | The WS pairing token wasn't sent. The runner reads `context/.ws-token` (or `$CURUNIR_WS_TOKEN`) — make sure the server wrote it and you're running from the repo root, or export `CURUNIR_WS_TOKEN`. |
| `1001 (going away)` mid-run | The server stopped/restarted. Restart `run.py` and re-run. |
| Judge tasks all `ERR … no JUDGE_MODEL/MODEL` | `ANTHROPIC_API_KEY` missing from `.env`, or set `JUDGE_MODEL` to a model whose key you have. |
| A data-spine task fails to fetch | The relevant skill's key is unset (e.g. `FRED_API_KEY` for R3). Anchored graders also need the same CLIs to run locally. |
| Connecting to wrong instance | Default is `localhost:8765`; pass `--host/--port` for a remote SUT. Make sure it was started with `CURUNIR_PERSONA=finance`. |

## Statuses

- **pass** — outcome contract satisfied.
- **fail** — contract violated.
- **pass-slow** — *correct but over a process budget* (wall-clock / tool-calls /
  turns). A distinct signal, never folded into fail: it is the exact axis that
  decomposition and perf work move. Tasks R1, F9 (tool-call budgets) and F11
  (10-min wall budget) carry budgets.
- **error** — the grader itself could not run (e.g. judge model unreachable).

## How the suite is built — the four sources

Tasks are generated from failure-first thinking, not "what features exist?":

1. **Regression tripwires** (`R1`–`R7`) — one deliberately easy task per core
   capability (each data CLI, web search, the financial-analysis and
   investment-memo orchestrators). Tripwires that go red the instant a refactor
   breaks a basic path.
2. **Failure-mode probes** (`F1`–`F11`) — the highest-value source. One prompt
   per known pathology of *this* design:
   - **mis-route** — `F1` (a recommendation must hit `investment-memo`, not
     `deep-research`), `F2` (an event seed must hit `catalyst-memo`).
   - **guardrails** — `F3` no regulated advice, `F4` never execute/simulate a
     trade, `F5` never leak private holdings to a third party.
   - **hallucination** — `F6` flag future/stale data instead of inventing,
     `F7` fetch fundamentals instead of reciting (cap must match live).
   - **dropped work** — `F8` show arithmetic + citations, `F10` surface a
     thesis's named disconfirming evidence, `F11` keep the Fact-Check Addendum.
   - **over-orchestration** — `F9` a trivial lookup must not spin up a memo.
3. **Composition points** (`C1`–`C4`) — tasks that force two+ capabilities to
   chain, where bugs cluster: two-ticker comparable (`C1`), catalyst →
   winners/losers + odds (`C2`), analysis pulling a real FRED discount rate
   (`C3`), position-tracking ⋈ tax-timing (`C4`).
4. **Grader-first** — applied as a filter: every task above has a crisp
   discriminating grader, or it was sharpened until it did.

## Anchoring (no hardcoded mutable answers)

Where the right answer moves with live data, the grader **recomputes** it from
the *same* CLI the agent uses, via the task's `anchor` field:

- `R2` trailing P/E, `F7` market cap → `yfinance/yfin.py multiples`
- `R4` CIK (frozen, exact) → `sec-edgar/edgar.py lookup`
- `C1` forward-P/E gap → `_pe_gap.py`

This keeps the eval valid across data reseeds and stops the eval and the agent
from silently drifting apart. Frozen facts (the CIK) are the only exact matches.

## The `Result` contract

The runner hands each grader a `Result` with `final_text`, `actions` (streamed
tool-call summary strings like `load_skill: investment-memo` or
`bash: python skills/yfinance/yfin.py quote AAPL`), `wall_ms`, `turns`,
`tokens_out`, `error`. Any harness that can populate those fields can reuse
`finance_graders.py` unchanged.

## Adding a task

1. Write the **grader first** — if you can't state a discriminating pass/fail
   check, the prompt is too vague; sharpen it.
2. Add the task dict to `finance_tasks.TASKS` with source + symptom **tags**.
3. If the answer can change with data, add an `anchor` instead of a constant.
4. If *how* it succeeds matters (speed/cost), add a `budget`.
