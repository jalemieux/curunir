# Curunir Evals

Two eval systems live here, at different maturity:

| System | Entry | Grading | Use |
|--------|-------|---------|-----|
| **Capture-only** | `run_evals.py` + `simple_evals.md` / `advanced_evals.md` | none — streamed to `eval_results/`, human-eyeballed | quick side-by-side model comparison; supports `--max-loops` and resume |
| **Graded harness** | `eval/harness/` + a persona suite | pure-function + LLM-judge graders, pass/fail/slow with a one-line reason, interactive HTML report | regression + failure-mode benchmarking of a persona |

The rest of this file documents the **graded harness** — the engine, how to run a
suite, the report, and the conventions every suite follows. Suites come in two
kinds, split by what they own:

- **Persona suites** (`eval/<persona>/`) own persona-level behavior: the
  persona's system-prompt specifics (guardrails, domain rules) and **skill
  routing across the persona's catalog** — given the full catalog, does a
  trigger prompt reach the *right* skill with no collision/shadowing?
- **Skill suites** (`eval/skills/<name>/`) own one skill's (or skill family's)
  contract in depth: method adherence, failure modes, output rules. They run
  against any persona whose allowlist carries the skill.

Per-suite specifics (which tasks, which fixtures) live in each suite's own README:

- **`eval/finance/`** → [`eval/finance/README.md`](finance/README.md) — 38 tasks: market data, the owner's balance sheet, and the live-data routing tripwires (FR1/PM1/PM2, migrated from the skill_routing suite; R1 is the yfinance routing contract).
- **`eval/default/`** → [`eval/default/README.md`](default/README.md) — 34 tasks in five families: **G** (the no-general-knowledge guardrail, #338), **S** (skill discovery — `load_skill` by name, no filesystem hunting, #451/#457), **K** (framework-kernel tripwires: scheduling persisted to `schedules.db` via `anchor_equals`, memory recall + delegate handoff from a seeded fixture, attachments, slash dispatch, multi-turn retention), **RS** (a routing sweep — one canonical trigger prompt per visible catalog skill not covered by a dedicated task, graded on routing only), and **WS/RR** (richer routing tripwires migrated from the web_search / reddit_research suites). The K+RS+WS/RR families make this suite the **model/quant-swap smoke test** (see below).
- **`eval/skills/`** → [`eval/skills/README.md`](skills/README.md) — the per-skill adherence suites:
  - [`skill_routing/`](skills/skill_routing/README.md) — adherence for the `yfinance` / `fred` / `polymarket` live-data skills (freshness citations, smallest subcommand, the CPI trap, priced probabilities; routing lives in `eval/finance/`).
  - [`reddit_research/`](skills/reddit_research/README.md) — curl-vs-web_fetch method adherence for the `reddit-research` skill (routing lives in `eval/default/`).
  - [`web_search/`](skills/web_search/README.md) — no-rediscovery-loop method adherence for the `web-search` skill (consumer/local-business lookups start with Brave, not a Google/Yelp/Reddit scrape; routing lives in `eval/default/`).

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

## Configuring `.env`

Two processes run during an eval, and **both** read the repo-root `.env`:

| Process | Loads | Needs |
|---------|-------|-------|
| the **SUT** — `python run.py` | `.env` via `load_dotenv()` | `MODEL` + that provider's key, plus every key the persona's skills call |
| the **runner** — `python eval/<suite>/run_<suite>_evals.py` | `.env` via `load_dotenv(REPO_ROOT / ".env")` | the **judge** key — and any skill key an `anchor` needs, since anchored graders shell out to the same CLI locally |

Start from the template, then fill in the two halves below:

```bash
cp .env.example .env      # then edit
```

### 1. The model under test

Whatever you'd normally run curunir with:

```bash
MODEL=anthropic/claude-sonnet-4-20250514
ANTHROPIC_API_KEY=sk-ant-...
# or: MODEL=openrouter/z-ai/glm-5.2   + OPENROUTER_API_KEY=sk-or-...
# a local llama.cpp / ollama server also needs API_BASE — see .env.eval.example
```

### 2. The judge

`llm_judge` tasks are graded by a model **separate from the SUT** (a model
grading its own output is an eval anti-pattern). It resolves as `$JUDGE_MODEL`,
else the default `anthropic/claude-sonnet-4-6` — never `$MODEL`. So the default
path needs only:

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

Judging with another provider means setting both the model and its key:

```bash
JUDGE_MODEL=openai/gpt-4o
OPENAI_API_KEY=sk-...
```

Without a usable judge key, `llm_judge` tasks report **`ERR`** (the grader
couldn't run), not `FAIL` — every other grader still produces a real result.

### Suite-specific skill keys

Data-fetching tasks call the persona's skills, which need their own keys in the
**SUT's** env (and in the runner's env too when a task carries an `anchor`, which
re-runs the same CLI locally to recompute the expected answer):

| Suite | Keys |
|-------|------|
| `eval/finance/` | `FRED_API_KEY`, `BRAVE_API_KEY`, `XAI_API_KEY`, `GEMINI_API_KEY` (yfinance / polymarket need network but no key) |
| `eval/default/` | `BRAVE_API_KEY` for the WS/RR tasks |
| `eval/skills/web_search/`, `eval/skills/reddit_research/` | `BRAVE_API_KEY` |
| `eval/skills/skill_routing/` | `FRED_API_KEY` |

`action_used` legs pass **without** these — they only check that the call was
*attempted* — so a keyless run still gives a valid routing signal; only the
grounded-answer and judged tasks degrade. Each suite's README lists its own keys.

### Quieting the instance under test (`.env.eval`, optional)

A normal boot runs the email / local-UI / portal channels and three background
loops (`MEMORY_EXTRACTION_ENABLED`, `DREAMING_ENABLED`, `SCHEDULER_ENABLED` —
all default-on), any of which can inject a turn or write into `context/memory/`
mid-suite. `.env.eval.example` is the eval profile for the SUT: every channel
but WS off, the loops off, plus small-context sizing and the local-model knobs
(`MODEL` / `API_BASE` / `VISION_MODEL`).

```bash
cp .env.eval.example .env.eval     # gitignored — safe to put real keys in
set -a; source .env.eval; set +a   # export, then boot from the same shell
python run.py
```

It is an **overlay, not a replacement**: the exported shell values win because
python-dotenv's `load_dotenv()` defaults to `override=False`, so `.env` still
supplies everything `.env.eval` leaves unset (API keys, `JUDGE_MODEL`, …).
Source it only in the SUT's shell — the runner loads `.env` in its own process,
so the judge keeps your real model and keys no matter what the SUT is running.
The same mechanism is what lets `eval/model_sweep.sh` pin `MODEL` per server
subprocess while the judge stays fixed.

## Quick start

The runner is a **headless WebSocket client** — it talks to a running curunir
instance over `ws://localhost:8765`, the same channel the CLI uses. Start the
server (with the persona you're evaluating), then run the suite from another
shell.

```bash
# 0. (one-time) activate the venv and configure .env — see "Configuring `.env`"
source .venv/bin/activate
cp .env.example .env   # skip if you already have one — this overwrites it
#    MODEL + its provider key for the SUT; ANTHROPIC_API_KEY for the judge; plus
#    whatever the persona's skills require (see the table above / suite README).

# 1. (one-time) stage the eval context baseline — see context.eval/README.md.
#    A missing context/identity.md means "not onboarded", so without this the
#    first eval turn is rewritten into an onboarding instruction. Use a
#    throwaway ./context; cp -R writes into it.
cp -R context.eval/. context/

# 2. Terminal A — start the system under test
#    optional: set -a; source .env.eval; set +a   # channels + background loops off
CURUNIR_PERSONA=<persona> python run.py

# 3. Terminal B — run the graded suite against it
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

## Swapping a model or quant? Run the smoke suite

The default suite doubles as the regression signal for a model or quant change:
the K family tripwires every framework capability (scheduling, memory,
delegation, attachments, slash dispatch, context retention) and the RS family
checks that natural phrasing still routes to each catalog skill — the two
things a weaker model or a bad quant breaks first.

```bash
CURUNIR_PERSONA=default python run.py                       # SUT on the candidate model
python eval/default/run_default_evals.py --fixture baseline # G + S + K + RS, graded
```

K2/K3 read the seeded fixture, so pass `--fixture baseline`. RS tasks are
routing-only and tolerate truncated execution (`allow_error`), so the sweep is
cheap; K1 writes a real schedule row, verifies it **in the store** with
`anchor_equals`, and removes it via the task's `cleanup`. Compare candidates
with `eval/model_sweep.sh` (below):
`SUITE=eval/default/run_default_evals.py EVAL_ARGS="--fixture baseline" eval/model_sweep.sh`.

## Model A/B sweep (`eval/model_sweep.sh`)

To compare the *same* suite across models without hand-swapping `MODEL` and
restarting the SUT each time, use the sweep. For each model it relaunches the
server with `MODEL=<m>`, waits for `:8765`, runs the suite, kills the server,
and moves on. The SUT reports its model in the welcome frame, so each run's
report auto-labels by model and lands in its own file under the suite's
`results/` — ready to diff.

```bash
# models from eval/model_sweep_models.txt:
eval/model_sweep.sh

# or name them explicitly (overrides the file):
eval/model_sweep.sh openrouter/z-ai/glm-5.2 anthropic/claude-sonnet-4-20250514

# a different suite, or a single task, via env:
SUITE=eval/finance/run_finance_evals.py EVAL_ARGS="--id R6" eval/model_sweep.sh
```

Model list resolution (first match wins): positional args → `MODELS_FILE` →
`eval/model_sweep_models.txt` → a built-in fallback. The file is one entry per
line — `<model-id>  [provider]` — where the optional second column is an
OpenRouter provider slug applied as `OPENROUTER_PROVIDER` for that run (same
field as `.env`; a line with no provider column runs with **no** provider pin
rather than inheriting `.env`'s). `#` comments and blank lines are ignored;
positional args are model ids only (no provider column). Other env knobs:
`SUITE` (default web-search), `EVAL_ARGS` (e.g. `--id WS3`), `WS_PORT`, `LOGDIR`
(SUT boot logs, default `/tmp/curunir_model_sweep_logs`), `PYTHON`.

The sweep passes `MODEL` **only** to each server subprocess — never to the shell
running the grader — so the judge stays fixed (see below) across all models
rather than each model grading its own output. It also holds the web-search
backend (Brave vs Gemini `SKILL.md`) constant: it sweeps *models*, not backends.
Each model needs its provider key in `.env`. The sweep restarts the SUT
repeatedly and leaves none running when it finishes — relaunch your own dev
instance afterward.

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
| `anchor_equals` | the anchor-queried **store** value equals a frozen `equals` — text-blind persistence check (a readback from chat history can't fake it) |
| `llm_judge` | a separate judge model rules PASS against a crisp `rubric` |
| `reconciles` | a stated balance sheet adds up (`assets − liabilities == net worth`) and matches an anchored truth |
| `composite` | ANDs a list of sub-graders; reports every sub-check |

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `received 1008 (policy violation) auth` | The WS pairing token wasn't sent. The runner reads `context/.ws-token` (or `$CURUNIR_WS_TOKEN`) — make sure the server wrote it and you're running from the repo root, or export `CURUNIR_WS_TOKEN`. |
| `1001 (going away)` mid-run | The server stopped/restarted. Restart `run.py` and re-run. |
| Judge tasks all `ERR … no JUDGE_MODEL/MODEL` | `ANTHROPIC_API_KEY` missing from `.env`, or set `JUDGE_MODEL` to a model whose key you have. |
| Memory/context changed mid-run, or a turn the suite never sent | A background loop or another channel is live on the SUT. Boot it under `.env.eval` (see [Quieting the instance under test](#quieting-the-instance-under-test-enveval-optional)) so only the WS channel and no loops are running. |
| A data task fails to fetch | The relevant skill's key is unset. Anchored graders also need the same CLIs to run locally. |
| Connecting to wrong instance | Default is `localhost:8765`; pass `--host/--port` for a remote SUT. Make sure it was started with the right `CURUNIR_PERSONA`. |
