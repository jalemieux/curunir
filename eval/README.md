# Curunir Evals

Two eval systems live here, at different maturity:

| System | Entry | Grading | Use |
|--------|-------|---------|-----|
| **Capture-only** | `run_evals.py` + `simple_evals.md` / `advanced_evals.md` | none — streamed to `eval_results/`, human-eyeballed | quick side-by-side model comparison; supports `--max-loops` and resume |
| **Graded harness** | `eval/harness/` + a persona suite | pure-function + LLM-judge graders, pass/fail/slow with a one-line reason, interactive HTML report | regression + failure-mode benchmarking of a persona |

The rest of this file documents the **graded harness**: [how to run a
suite](#run-an-eval) end to end, [how to configure it](#configuring-the-environment),
how to read the report, and the conventions every suite follows. Suites come in
two kinds, split by what they own:

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

## Run an eval

An eval is always **two processes**: the **system under test** (SUT) — an
ordinary `python run.py` instance — and the **suite**, a headless WebSocket
client that drives it over `ws://localhost:8765` exactly like the CLI does. So
you need two shells, both at the repo root with the venv activated.

**Which suite do I run?**

| You changed… | Run |
|--------------|-----|
| the model, the quant, or the provider | `eval/default/` with `--fixture baseline` — the kernel + routing smoke test |
| a persona's prompts or skill allowlist | that persona's suite — `eval/default/`, `eval/finance/` |
| one skill's `SKILL.md` or its CLI | that skill's suite under `eval/skills/` |
| the harness itself (`eval/harness/`) | `python eval/harness/test_runner_sync.py` — no server, no keys, no tokens |

### 1. Install, and configure `.env`

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # skip if you already have one — this overwrites it
```

Then edit `.env`. Three things matter, and the first two are non-negotiable:

```bash
MODEL=anthropic/claude-sonnet-4-20250514   # the model being evaluated (the SUT)
ANTHROPIC_API_KEY=sk-ant-...               # its key — and, by default, the judge's too
FRED_API_KEY=...                           # + whatever the suite's skills call
```

The judge (for `llm_judge` tasks) defaults to `anthropic/claude-sonnet-4-6`, so
`ANTHROPIC_API_KEY` already covers it; to judge with another provider set
`JUDGE_MODEL` **and** that provider's key. Which skill keys a given suite needs
is in [Configuring the environment](#configuring-the-environment) below — a run
with keys missing still works, it just downgrades the affected tasks.

### 2. Point the SUT at a different config (optional)

Skip this unless you are evaluating a **local/other model** or want the instance
silent while it is graded. `.env.eval` overrides `.env` for one boot:

```bash
cp .env.eval.example .env.eval   # gitignored — real keys are fine here
```

It ships with every channel but WS off and the three background loops off, plus
local-model (`MODEL` / `API_BASE`) and small-context knobs to edit. Full
semantics: [Overriding `.env` for the SUT](#overriding-env-for-the-sut-enveval-optional).

### 3. Stage a clean `context/`

```bash
cp -R context.eval/. context/
```

A missing `context/identity.md` reads as "not onboarded", and the first eval
turn gets rewritten into an onboarding instruction — every task then fails for
the wrong reason. `cp -R` **writes into** your `context/`, so use a throwaway or
staging one. See [`context.eval/README.md`](../context.eval/README.md).

### 4. Boot the SUT — shell A

```bash
set -a; source .env.eval; set +a          # only if you did step 2
CURUNIR_PERSONA=default python run.py     # the persona the suite targets
```

Wait for the boot lines before moving on:

```
Background loops: extraction=false dreaming=false scheduler=false   # under .env.eval
Starting 1 channel(s): ws                                           # ws is the one that matters
```

Without `.env.eval` those read `true` and more channels start; the suite still
runs, it just isn't isolated. Leave the process up — the persona must match the
suite, since `eval/finance/` against a `default` instance grades a persona that
was never loaded.

### 5. Smoke-test the wiring — shell B

Two checks that cost nothing, before you spend tokens on 30+ tasks.

```bash
python eval/harness/test_runner_sync.py            # the harness itself; no server needed
python eval/default/run_default_evals.py --list    # the suite loads; no server, no keys
```

`--list` prints `34 tasks` and the id/grader/tags/name table. Then run **one**
cheap task end-to-end to prove the socket, the token, and the persona are right:

```bash
python eval/default/run_default_evals.py --id S1 -v
```

`-v` streams the agent's tool calls live, so a misconfiguration is obvious
rather than showing up as a bare `FAIL`. If this connects and grades, the full
suite will too. If it doesn't, go to [Troubleshooting](#troubleshooting).

### 6. Run the suite — shell B

```bash
python eval/default/run_default_evals.py --fixture baseline
```

One status line per task while it runs, then a `SUMMARY` block and the path to
the report. `--fixture baseline` seeds `fixtures/memory/baseline/` into
`context/memory/` for the tasks that read seeded memory (K2/K3) and restores
your real memory on exit — omit it and those tasks fail legitimately.

Other suites, same shape:

```bash
python eval/finance/run_finance_evals.py               # persona suite
python eval/skills/web_search/run_web_search_evals.py  # skill suite
```

### 7. Read the report

```bash
open eval/default/results/default-<ts>-<model>.html
```

The HTML report is the primary artifact — per-task cards with the full grader
breakdown, prompt, every tool call, and the agent's complete final text. See
[Reading the output](#reading-the-output) for what's in it and the `.json` /
`.md` siblings.

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

## Configuring the environment

The reference layer behind step 1 of the runbook: what each variable is for, who
reads it, and what breaks when it is missing.

Two env files are in play, both with a tracked `.example` to copy from:

| File | Copy from | Role |
|------|-----------|------|
| `.env` | `.env.example` | **required** — API keys, `MODEL`, `JUDGE_MODEL`. Read by *both* processes below |
| `.env.eval` | `.env.eval.example` | **optional** — the eval profile for the instance under test (channels + background loops off, local-model knobs). Sourced into the SUT's shell, where it **overrides** `.env` for that boot; see [below](#overriding-env-for-the-sut-enveval-optional) |

Two processes run during an eval, and **both** read the repo-root `.env`:

| Process | Loads | Needs |
|---------|-------|-------|
| the **SUT** — `python run.py` | `.env` via `load_dotenv()` | `MODEL` + that provider's key, plus every key the persona's skills call |
| the **runner** — `python eval/<suite>/run_<suite>_evals.py` | `.env` via `load_dotenv(REPO_ROOT / ".env")` | the **judge** key — and any skill key an `anchor` needs, since anchored graders shell out to the same CLI locally |

### The model under test

Whatever you'd normally run curunir with:

```bash
MODEL=anthropic/claude-sonnet-4-20250514
ANTHROPIC_API_KEY=sk-ant-...
# or: MODEL=openrouter/z-ai/glm-5.2   + OPENROUTER_API_KEY=sk-or-...
# a local llama.cpp / ollama server also needs API_BASE — see .env.eval.example
```

### The judge

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

### Overriding `.env` for the SUT (`.env.eval`, optional)

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

**`.env.eval` wins over `.env`.** Anything it sets overrides your normal
value — `MODEL`, `MAX_HISTORY_CHARS`, `EMAIL_ENABLED`, whatever — for that boot
only, and anything it *doesn't* set still comes from `.env` (API keys,
`JUDGE_MODEL`, …). Nothing is edited and nothing is lost; `.env` stays your
day-to-day config.

The precedence comes from the `set -a; source` step, not from a loader change:
sourcing exports the values into the shell, and `run.py`'s `load_dotenv()`
defaults to `override=False`, so it will not clobber a var that is already set.
Concretely, with `MODEL` in both files:

```
MODEL=from_eval        # .env.eval — used
FRED_API_KEY=...       # .env only — still used
```

Two consequences worth knowing:

- **Same shell, before `run.py`.** A new terminal has none of it — re-source
  there. To undo, just open a fresh shell.
- **Only what it sets.** A var commented out in `.env.eval` falls through to
  `.env`; to *clear* one that `.env` pins, give it an empty value
  (`OPENROUTER_PROVIDER=`), which counts as set and so still wins.

Source it only in the SUT's shell — the runner loads `.env` in its own process,
so the judge keeps your real model and keys no matter what the SUT is running.
The same mechanism is what lets `eval/model_sweep.sh` pin `MODEL` per server
subprocess while the judge stays fixed.

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
running the grader — so the judge stays fixed ([The judge](#the-judge)) across all models
rather than each model grading its own output. It also holds the web-search
backend (Brave vs Gemini `SKILL.md`) constant: it sweeps *models*, not backends.
Each model needs its provider key in `.env`. The sweep restarts the SUT
repeatedly and leaves none running when it finishes — relaunch your own dev
instance afterward.

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
| Memory/context changed mid-run, or a turn the suite never sent | A background loop or another channel is live on the SUT. Boot it under `.env.eval` (see [Overriding `.env` for the SUT](#overriding-env-for-the-sut-enveval-optional)) so only the WS channel and no loops are running. |
| A data task fails to fetch | The relevant skill's key is unset. Anchored graders also need the same CLIs to run locally. |
| Connecting to wrong instance | Default is `localhost:8765`; pass `--host/--port` for a remote SUT. Make sure it was started with the right `CURUNIR_PERSONA`. |
