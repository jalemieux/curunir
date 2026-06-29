# Curunir Evals

Two eval systems live here, at different maturity:

| System | Entry | Grading | Use |
|--------|-------|---------|-----|
| **Capture-only** | `run_evals.py` + `simple_evals.md` / `advanced_evals.md` | none — streamed to `eval_results/`, human-eyeballed | quick side-by-side model comparison; supports `--max-loops` and resume |
| **Graded harness** | `eval/harness/` + a persona suite | pure-function + LLM-judge graders, pass/fail/slow with a one-line reason, interactive HTML report | regression + failure-mode benchmarking of a persona |

The rest of this file documents the **graded harness** — the engine, how to run a
suite, the report, and the conventions every suite follows. Per-persona specifics
(which tasks, which fixtures) live in each suite's own README:

- **`eval/finance/`** → [`eval/finance/README.md`](finance/README.md) — ~34 tasks, market data + the owner's balance sheet.
- **`eval/default/`** → the default persona; `default_tasks.py` is an empty `TASKS` placeholder to be populated from the capture-only prompts.
- **`eval/skill_routing/`** → [`eval/skill_routing/README.md`](skill_routing/README.md) — routing + adherence for the `yfinance` / `fred` / `polymarket` live-data skills.
- **`eval/reddit_research/`** → [`eval/reddit_research/README.md`](reddit_research/README.md) — routing + curl-vs-web_fetch method adherence for the `reddit-research` skill.

## The graded engine (`eval/harness/`)

Persona-agnostic. A persona suite is a **thin shim** that builds a `SuiteConfig`
and calls `runner.main`; everything else is shared.

| File | Role |
|------|------|
| `harness/graders.py` | Pure graders `(result, spec) -> (status, why)`; the `GRADERS` dispatch + `grade()` / `grade_detailed()`. Includes `reconciles` (a balance sheet must add up) |
| `harness/runner.py` | `SuiteConfig` + the generic engine: drives the WS channel, builds a `Result`, grades, writes JSON/MD/HTML. Supports multi-turn `prompts` and `--fixture` seeding |
| `harness/test_runner_sync.py` | Zero-token tests for the runner's WS frame handling (multi-turn + `reconciles` included; no SUT needed) |

A shim is ~40 lines — see `eval/finance/run_finance_evals.py`:

```python
SUITE = runner.SuiteConfig(
    name="finance",                 # report filename prefix: finance-<ts>-<model>.*
    title="Finance Persona Evals",  # HTML header / <title>
    tasks=TASKS,                    # from <persona>_tasks.py
    results_dir=Path(__file__).parent / "results",
    fixture_memory_dir=Path(__file__).parent / "fixtures" / "memory",  # for --fixture
)
runner.main(SUITE)
```

## Quick start

The runner is a **headless WebSocket client** — it talks to a running curunir
instance over `ws://localhost:8765`, the same channel the CLI uses. Start the
server (with the persona you're evaluating), then run the suite from another
shell.

```bash
# 0. (one-time) activate the venv and set keys in .env
source .venv/bin/activate
#    .env needs ANTHROPIC_API_KEY (the Claude judge) plus whatever the persona's
#    skills require (see the persona's README).

# 1. Terminal A — start the system under test
CURUNIR_PERSONA=<persona> python run.py

# 2. Terminal B — run the graded suite against it
python eval/<persona>/run_<persona>_evals.py
```

That prints a live status line per task and, at the end, a summary plus the path
to a saved report under `eval/<persona>/results/`.

### Flags (run a subset — cheaper)

All suites accept the same flags (`eval.harness.runner.build_parser`):

```bash
... --id R6,F9          # just these task ids
... --tag regression    # only tasks whose tag matches this regex
... --list              # print id/grader/tags/name and exit — no server needed
... --no-grade          # capture only, skip grading (like the legacy harness)
... -v / --verbose      # stream each task's tool calls + text live, then the grade
... --fixture NAME      # seed fixtures/memory/NAME/ into context/memory/ (local SUT only)
... --host h --port p   # remote SUT (default localhost:8765)
```

The full suite spends real model tokens on the SUT, so use `--id` / `--tag`
while iterating and run everything only for a complete baseline.

### Verify the runner itself (no server, no tokens)

The runner's WS frame handling is covered by a fake-socket test that replays the
exact server frame sequence — run it after touching `eval/harness/runner.py`:

```bash
python eval/harness/test_runner_sync.py
```

## The judge model

`llm_judge` tasks are graded by a **model separate from the system under test**
(a model grading its own output is an eval anti-pattern). The shim loads `.env`,
so the default `anthropic/claude-sonnet-4-6` works as long as `ANTHROPIC_API_KEY`
is set. Override with `JUDGE_MODEL=<litellm-model-id>` (and that provider's key
in the env).

## Reading the output

Each task prints `PASS` / `FAIL` / `SLOW` / `ERR` and a one-line reason. Three
report files are written per run under `results/`, prefixed by the suite name:

- **`<persona>-<ts>-<model>.html`** — the primary human report. A self-contained
  page (no external assets) with one collapsible card per task:
  - the **full grader breakdown** — every sub-check of a composite with its own
    pass/fail dot and reason, not just the first failure;
  - the full prompt, every tool call, attachments, and the agent's **complete
    final text**;
  - **empty responses are flagged** with a red banner ("EMPTY RESPONSE — the
    agent returned no text. It ran N turns and emitted M tool calls") plus any
    runner error — so a blank answer is loud, not silent;
  - per-task server stats (wall, iterations, llm calls, tokens);
  - filter by status, filter by tag, free-text search, expand/collapse all.

  Open it in a browser: `open results/<persona>-<ts>-<model>.html`.
- `<persona>-<ts>-<model>.json` — the same capture as structured data
  (`checks`, `final_text`, `actions`, `attachments`, `stats`, `error`), for
  programmatic diffing across runs.
- `<persona>-<ts>-<model>.md` — a lightweight `id | name | status | why` table
  for quick GitHub viewing / diffing.

For a live trace while it runs, add `-v`. A failure is the agent's, not the
harness's — the captured `final_text` and `actions` show exactly what the model
did.

## Statuses

- **pass** — outcome contract satisfied.
- **fail** — contract violated.
- **pass-slow** — *correct but over a process budget* (wall-clock / tool-calls /
  turns). A distinct signal, never folded into fail: it is the exact axis that
  decomposition and perf work move. A task opts in with a `budget`.
- **error** — the grader itself could not run (e.g. judge model unreachable).

## How a suite is built — the four sources

Tasks are generated from failure-first thinking, not "what features exist?":

1. **Regression tripwires** — one deliberately easy task per core capability.
   Tripwires that go red the instant a refactor breaks a basic path.
2. **Failure-mode probes** — the highest-value source. One prompt per *known
   pathology of this design*: mis-routing, dropped guardrails, hallucination,
   dropped work, over-orchestration.
3. **Composition points** — tasks that force two+ capabilities to chain, where
   bugs cluster.
4. **Grader-first** — applied as a filter: every task above has a crisp,
   discriminating grader, or it was sharpened until it did.

Adding a task: **write the grader first** — if you can't state a discriminating
pass/fail check, the prompt is too vague. Add the task dict to the suite's
`TASKS` with source + symptom **tags**. If the answer can change with data, add
an `anchor` instead of a constant. If *how* it succeeds matters (speed/cost),
add a `budget`.

## Anchoring (no hardcoded mutable answers)

Where the right answer moves with live data, the grader **recomputes** it from
the *same* CLI/source the agent uses, via the task's `anchor` field
(`{cmd, json_path}`). This keeps the eval valid across data reseeds and stops
the eval and the agent from silently drifting apart. Frozen facts (e.g. a CIK)
stay exact matches; everything live is tolerance-checked. See the persona README
for that suite's anchors.

## The `Result` contract

The runner hands each grader a `Result` with `final_text`, `actions` (streamed
tool-call summary strings like `load_skill: investment-memo` or
`bash: python skills/yfinance/yfin.py quote AAPL`), `attachments`, `wall_ms`,
`turns`, `tokens_out`, `stats`, `error`. Any harness that can populate those
fields can reuse `eval/harness/graders.py` unchanged.

## The graders

| Grader | Passes when |
|--------|-------------|
| `exact_match` | a regex-extracted token equals `expected` (optional `normalize`) |
| `numeric_tolerance` | the answer holds a number within `tolerance_pct` of `expected` (anchorable) |
| `set_match` | every item in `expected` appears (case-insensitive); `groups` = one-of-each |
| `regex_present` | all `require` regexes match and no `forbid` regex matches |
| `action_used` | routing contract: `require` / `require_any` actions ran, `forbid` didn't |
| `llm_judge` | a separate judge model rules PASS against a crisp `rubric` |
| `reconciles` | a stated balance sheet adds up (`assets − liabilities == net worth`) and matches an anchored truth |
| `composite` | ANDs a list of sub-graders; reports every sub-check |

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `received 1008 (policy violation) auth` | The WS pairing token wasn't sent. The runner reads `context/.ws-token` (or `$CURUNIR_WS_TOKEN`) — make sure the server wrote it and you're running from the repo root, or export `CURUNIR_WS_TOKEN`. |
| `1001 (going away)` mid-run | The server stopped/restarted. Restart `run.py` and re-run. |
| Judge tasks all `ERR … no JUDGE_MODEL/MODEL` | `ANTHROPIC_API_KEY` missing from `.env`, or set `JUDGE_MODEL` to a model whose key you have. |
| A data task fails to fetch | The relevant skill's key is unset. Anchored graders also need the same CLIs to run locally. |
| Connecting to wrong instance | Default is `localhost:8765`; pass `--host/--port` for a remote SUT. Make sure it was started with the right `CURUNIR_PERSONA`. |
