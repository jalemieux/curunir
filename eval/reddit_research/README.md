# Reddit-Research Skill Evals

Behavioral eval suite for the **`reddit-research`** skill — the two-step
pipeline that discovers Reddit posts (Brave Search `site:reddit.com` / Reddit's
own `search.json`) and extracts full post + comment content by appending `.json`
to a Reddit URL and parsing it with `curl` + `jq`. It exists to measure the two
things that actually matter for this skill:

1. **Routing** — given a request for live Reddit/community voice (which *cannot*
   be answered from training data), does the agent **load the skill and actually
   fetch** instead of fabricating sentiment from memory? (Graded with
   `action_used`: the routing contract *is* the action — `load_skill:
   reddit-research` + a real `curl`.)
2. **Method adherence** — does extraction use the documented **curl + JSON-API**
   method, **not** the documented mistakes (a headless browser, or
   `web_fetch`/trafilatura pointed at a `.json` URL, which return mangled
   output)? (Graded with `action_used` require/forbid + an `llm_judge` for
   whether the synthesis is *grounded* in fetched discussion vs. generic.)

> The shared machinery — the graded engine, run flags, report format, statuses,
> the grader catalog, anchoring, and the `Result` contract — is documented once
> in [`eval/README.md`](../README.md). This file covers only what's specific to
> this suite. The design mirrors [`eval/skill_routing/`](../skill_routing/).

## Why these graders are allowed to assert "the skill was used"

The house rule is *grade outcomes, not internals* — but for this skill the
routing **and the method** are the outcome contract. Reddit blocks headless
browsers, so "it loaded the skill and curled the public JSON API" (rather than
fabricating an answer or scraping with a tool that returns garbage) is the
user-visible promise, not an implementation detail. So `action_used` requiring
`load_skill: reddit-research` + `curl`, and forbidding a `web_fetch` of a Reddit
URL, is a legitimate contract — the same exception the skill-routing and finance
suites carve out for routing.

## Files

| File | Role |
|------|------|
| `reddit_research_tasks.py` | Tasks as data: `{id, name, intent, expected, tags, prompt, max_loops, grader, spec, budget}`. IDs: `RR*` |
| `run_reddit_research_evals.py` | Thin shim: builds the `SuiteConfig` and calls `eval.harness.runner.main` |
| `results/` | Timestamped JSON + markdown + HTML reports (git-ignored) |

## Quick start

```bash
# 0. (one-time) activate the venv and set keys in .env
source .venv/bin/activate
#    .env needs:  BRAVE_API_KEY     (discovery — the agent can't fetch without it)
#                 ANTHROPIC_API_KEY (the llm_judge for RR2/RR4)

# 1. Terminal A — start the system under test
CURUNIR_PERSONA=marketing python run.py

# 2. Terminal B — run the graded suite against it
python eval/reddit_research/run_reddit_research_evals.py              # full suite
python eval/reddit_research/run_reddit_research_evals.py --tag routing
python eval/reddit_research/run_reddit_research_evals.py --id RR1,RR3 # iterate cheap
python eval/reddit_research/run_reddit_research_evals.py --list       # no server needed
```

`marketing` and `finance` both allowlist `reddit-research`; any persona whose
catalog includes it works.

## The tasks

| id | source | what it catches |
|----|--------|-----------------|
| RR1 | regression | a Reddit-sentiment request **routes + curls** (≤12 actions) |
| RR2 | failure-mode | sentiment is **fetched, not recited** — answer cites Reddit markers and reads grounded (judge) |
| RR3 | failure-mode | extraction uses **curl**, **not** `web_fetch` on a Reddit/`.json` URL |
| RR4 | composition | discovery → extraction → **synthesis** into 3 grounded complaints (judge) |
| RR5 | failure-mode | **capability-triggered** routing — auto-routes with the word "Reddit" *unnamed* |

## No anchoring — and why

Reddit content is live and non-deterministic (threads come and go, scores
change), so **no task anchors an exact value**. The graders check *routing*,
*method*, and *grounding markers* — all of which stay valid as threads appear
and disappear. The `llm_judge` checks (RR2/RR4) see only the agent's final text,
so they verify the answer *looks* grounded (named subreddits, specific threads,
concrete complaints) — they can confirm specificity and Reddit-grounding, not
the literal truth of each quoted complaint.

## Notes & gotchas

- **Bash actions are truncated to ~57 chars** in the streamed summary, so
  `action_used` substrings must appear early in the command. `curl`,
  `reddit.com`, and `brave` sit near the front of the skill's commands and match
  reliably; the required `User-Agent` header and the trailing `.json` sit past
  the cutoff and are therefore **not graded** (they're internal details the
  truncation would defeat anyway). `load_skill: reddit-research` and a full
  `web_fetch: <url>` are **not** truncated.
- **Routing graders pass without keys** — `action_used` only checks the call was
  *attempted*. But the prompts genuinely need `BRAVE_API_KEY` + network for the
  agent to produce a grounded answer, and the judges (RR2/RR4) need a judge key
  in **both** the SUT's env and this runner's env.
- **RR5 is intentionally strict** — it requires `load_skill: reddit-research`
  for a community-voice request that never says "Reddit". If the agent instead
  routes to `web-search`/`deep-research`, RR5 fails *by design* — it's the test
  of the description's capability-first trigger. Relax it to `require_any` if you
  want to accept either path.
- The full suite spends real model tokens on the SUT; iterate with `--id` /
  `--tag`. See [`eval/README.md`](../README.md) for the report format and flags.
```
