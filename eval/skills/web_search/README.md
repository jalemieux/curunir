# Web-Search Method Evals

Behavioral eval suite for the search method fixed in **PR #458 / #450**: for
consumer and local-business lookups (salons, restaurants, local reviews,
Yelp/Reddit threads), the agent must **start with the `web-search` skill (Brave
Search API)** instead of re-discovering, every session, that Google/Yelp/Reddit
block scraping. The old behaviour burned ~5-6 tool calls (raw-`curl` of
`google.com/search`, `web_fetch` of Yelp → `403`, Reddit → blocked) before
falling back to Brave — the tool that actually works. This suite grades the
**no-rediscovery-loop method**: does the agent avoid `web_fetch`/raw-`curl`
against Google search / Yelp / Reddit, ground its answer in fetched results,
and chain search→fetch onto a *non-blocked* URL? (Graded with `action_used`
`forbid` on those hosts, plus a `max_actions` budget — a correct answer that
still burned the 5-6-call loop scores **PASS-SLOW**, the exact axis this PR
moves.)

The pure **routing** contracts (does a consumer lookup reach the skill at
all?) live in the **default persona suite** — [`eval/default/`](../../default/)
carries WS1 and WS5 — because routing is a property of the persona's catalog,
not the skill. See the taxonomy in [`eval/README.md`](../../README.md).

> The shared machinery — the graded engine, run flags, report format, statuses,
> the grader catalog, anchoring, and the `Result` contract — is documented once
> in [`eval/README.md`](../../README.md). This file covers only what's specific to
> this suite. The design mirrors [`eval/skills/reddit_research/`](../reddit_research/).

## Relationship to `tests/test_skills.py`

PR #458 ships a static guard
(`tests/test_skills.py::TestWebSearchScrapingHint`) that the steering *text*
(the named blocked sites) survives in the manifest `description` and the skill
body. That's a cheap regression on the *string*. This suite grades the thing the
unit test cannot: the agent's actual end-to-end **routing behaviour** on a real
consumer lookup — that the hint actually changes what the model does, not just
that it's present.

## Why these graders are allowed to assert "the skill was used"

The house rule is *grade outcomes, not internals* — but for this fix the routing
**and the avoidance of the blocked-site scrape** are the user-visible outcome
contract. The whole point of #450 is "reach Brave first, don't re-curl Yelp into
a 403." So `action_used` requiring `load_skill: web-search` + a Brave `curl`, and
forbidding a `web_fetch`/`curl` of Google-search/Yelp/Reddit, is a legitimate
contract — the same exception the skill-routing, reddit-research, and finance
suites carve out for routing.

## Files

| File | Role |
|------|------|
| `web_search_tasks.py` | Tasks as data: `{id, name, intent, expected, tags, prompt, max_loops, grader, spec, budget}`. IDs: `WS*` |
| `run_web_search_evals.py` | Thin shim: builds a `SuiteConfig` and calls `eval.harness.runner.main` |
| `results/` | Per-run JSON/MD/HTML reports (git-ignored) |

## Tasks (the four eval-design sources)

Ids are stable, so the gaps are deliberate: WS1/WS5 (the routing tripwires)
migrated to [`eval/default/`](../../default/).

| id | source | symptom | what it catches |
|----|--------|---------|-----------------|
| `WS2` | failure-mode | no-rediscovery | a restaurant lookup must NOT `web_fetch`/`curl` Google/Yelp/Reddit |
| `WS3` | failure-mode | answer-from-memory | a current-listing ask must be searched, not invented; answer grounded (judge) |
| `WS4` | composition | search-then-fetch | Brave first, then fetch only a *non-blocked* result URL; concrete synthesis (judge) |

Brave results are live and non-deterministic, so **no task anchors an exact
value** — the graders check routing, the forbidden-scrape avoidance, and
grounding, all of which stay valid as listings change. Every routing/method task
carries a `max_actions` budget so a correct-but-loopy run surfaces as PASS-SLOW
rather than a clean pass.

## Running

```bash
# Terminal A — the system under test (any persona that allowlists web-search)
CURUNIR_PERSONA=default python run.py

# Terminal B — the graded suite
python eval/skills/web_search/run_web_search_evals.py            # all tasks
python eval/skills/web_search/run_web_search_evals.py --tag method
python eval/skills/web_search/run_web_search_evals.py --id WS2,WS3
python eval/skills/web_search/run_web_search_evals.py --list     # no server needed
```

**Keys.** The `action_used` graders only check the call was
*attempted*, so they pass without a key. But the prompts genuinely need
`BRAVE_API_KEY` + network in the **SUT's** env to produce a grounded answer, and
the `llm_judge` checks (WS3, WS4) need `JUDGE_MODEL`/`MODEL` + a key in the env
where the **runner** runs (it loads `.env`, same as `run.py`). The judge model is
deliberately *not* the SUT model.

## Action-matching note

The server truncates a `bash` command to ~57 chars in the streamed action
summary, so `action_used` substrings must appear early. The Brave call is
`curl -s "https://api.search.brave.com/res/v1/web/search?q=...`, so `curl` and
`api.search.brave.com` match reliably. The forbidden raw-scrapes
(`google.com/search`, `yelp.com`, `reddit.com`) sit near the front of their
commands and match; a full `web_fetch: <url>` and `load_skill: web-search` are
not truncated. The `forbid` lists never forbid a bare `curl` (the Brave call is a
curl) — only the hosts that appear exclusively when scraping a blocked site.
