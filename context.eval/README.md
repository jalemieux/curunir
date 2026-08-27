# context.eval — baseline `./context` for eval runs

A clean, neutral `./context` template you stage **before booting** an instance
for evaluation. Its sole purpose is to make a fresh instance look *already
onboarded* so the onboarding flow does not run on the first eval turn.

## Why this exists

The agent treats a **missing `context/identity.md`** as the "not yet onboarded"
signal: the first user turn of a fresh session is rewritten into an onboarding
instruction (`src/agent/agent.py`). `context.default/` — the real-user bootstrap
source — deliberately omits `identity.md` so genuine first-run users get
onboarding. Evals need the opposite, so this template ships a neutral
`identity.md` in addition to the same neutral memory baseline.

## What's inside

- `identity.md` — minimal, neutral persona. Marks the instance onboarded;
  persona behavior under test is layered from `personas/<CURUNIR_PERSONA>/prompts/`.
- `memory/` — the same neutral baseline as `context.default/memory/` (empty
  topical templates + README + `summaries/timeline.md`). Per-suite fixtures
  (`eval/<suite>/fixtures/memory/`, `--fixture`) seed real memory on top at run time.
- `schedules.json` — empty, so no scheduled tasks fire during a run.

## Usage

Stage it into `./context` from the repo root, then boot and run the suite:

```bash
# one-time, before booting the instance under test
cp -R context.eval/. context/          # does not clobber an existing context/ if you skip this
cp .env.eval.example .env.eval         # then edit: model, api_base, context sizes

set -a; source .env.eval; set +a       # exported values beat .env (load_dotenv override=False)
python run.py                          # boots already-onboarded; no onboarding turn
# in another shell:
python eval/finance/run_finance_evals.py     # or eval/default/run_default_evals.py
```

`.env.eval` is the environment half of the same idea: it turns off every
channel except WS (the harness drives `ws://localhost:8765`) and the three
background loops — `MEMORY_EXTRACTION_ENABLED`, `DREAMING_ENABLED`,
`SCHEDULER_ENABLED`, all default-on in `run.py` — so nothing calls the model
or writes into `context/memory/` while a suite is being graded. It is an
overlay, not a replacement: `run.py`'s `load_dotenv()` still supplies your API
keys from `.env`, and the eval harness loads `.env` in its own process, so the
LLM judge is unaffected by whatever model the instance under test is running.

Use a throwaway/staging `./context` for this — `cp -R` writes into it. `context/`
is gitignored, so staging the template never shows up as a change.
