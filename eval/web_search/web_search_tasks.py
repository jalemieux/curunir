"""Web-search routing eval suite — tasks as data.

Scope: the routing reflex added in PR #458 / #450 — for consumer and
local-business lookups (salons, restaurants, local reviews, Yelp/Reddit threads),
the agent must **start with the `web-search` skill (Brave Search API)** instead
of re-discovering, every session, that Google/Yelp/Reddit block scraping. The old
behaviour burned ~5-6 tool calls (`bash` raw-`curl` of `google.com/search`,
`web_fetch` of Yelp → `403`, Reddit → blocked) before falling back to Brave — the
tool that actually works. The fix lands the steer at the always-on surfaces (the
skill-manifest `description` + persona prompts), so the contract is *behavioral*:
does the agent route to Brave first and NOT scrape the blocked sites?

`tests/test_skills.py::TestWebSearchScrapingHint` already guards that the hint
*text* survives edits (a static regression). This suite grades the thing that
unit test cannot: the agent's actual end-to-end routing behaviour on a real
consumer lookup.

The suite answers the two questions worth grading for this fix:

  1. ROUTING — given a consumer/local-business lookup, does the agent load the
     `web-search` skill and actually run a Brave search? Graded with
     `action_used` — the routing contract IS the action (`load_skill: web-search`
     + a `curl` to the Brave API). A bare-memory answer or a raw-scrape attempt
     does neither.

  2. NO REDISCOVERY LOOP — does the agent AVOID the documented mistake of
     `web_fetch`/raw-`curl` against the bot-blocked sites (Google search, Yelp,
     Reddit)? Graded with `action_used` `forbid` (no `web_fetch`/`curl` of those
     hosts) plus a `max_actions` budget — a correct answer that still burned the
     5-6-call rediscovery loop scores PASS-SLOW, the exact signal this PR moves.

Run under any persona that allowlists `web-search` and carries the reflex prompt
(`default`, `finance`, `marketing`, `companion` all do — `default` is the most
realistic routing test because the full catalog is present and the agent must
still pick `web-search`):

    CURUNIR_PERSONA=default python run.py                 # SUT, one shell
    python eval/web_search/run_web_search_evals.py        # this suite, another

Routing graders (`action_used`) check the call was ATTEMPTED, so they pass even
without a key — but the prompts genuinely need `BRAVE_API_KEY` + network for the
agent to produce a grounded answer, and the `llm_judge` checks need a judge key
(see README). Brave results are live and non-deterministic, so NO task anchors an
exact value — they grade routing, method, and grounding, all of which stay valid
as listings change.

Tasks are organised by the four eval-design sources:
  1. Regression tripwires  — the core routing path must never break (WS1)
  2. Failure-mode probes    — raw-scrape the blocked sites (WS2), answer-from-
                              memory (WS3), capability-triggered routing with the
                              tool un-named (WS5)
  3. Composition points     — search → fetch the right (non-blocked) URL →
                              synthesize seam (WS4)
  4. Grader-first           — applied as a filter (every task has a crisp grader)

Each task dict:
    id       — stable short id (WS*)
    name     — kebab slug
    intent   — what failure this catches (report only)
    expected — the contract in one line (report only)
    tags     — source tag + symptom tag (for --tag filtering)
    prompt   — the exact user message sent to the agent
    max_loops— hard tool-call stop for this task
    grader   — dispatch key into eval.harness.graders.GRADERS
    spec     — grader config
    budget   — optional process budget; a correct run over it scores PASS-SLOW

NOTE on action matching: the server truncates a `bash` command to ~57 chars in
the streamed action summary, so substrings used in `action_used` must appear
early in the command. The Brave call is `curl -s "https://api.search.brave.com/
res/v1/web/search?q=...` — so `curl` and `api.search.brave.com` match reliably.
For the forbidden raw-scrapes, `google.com/search`, `yelp.com`, and `reddit.com`
all sit near the front of their commands and match; a full `web_fetch: <url>`
and `load_skill: web-search` are NOT truncated.

The `forbid` lists deliberately do NOT forbid a bare `curl` (the Brave call is a
curl) — they forbid the *hosts* that only appear when the agent scrapes a blocked
site (`yelp.com`, `google.com/search`) or `web_fetch`es one of the trio.
"""

# Hosts that only appear in an action when the agent is scraping a bot-blocked
# site — the rediscovery loop this PR exists to skip. None of these appear in the
# legitimate Brave call (api.search.brave.com), so forbidding them can't
# false-positive on a correct search.
_BLOCKED_SCRAPE = [
    "google.com/search",            # raw-curl of Google results
    "yelp.com",                     # curl or web_fetch of Yelp (any tool)
    "web_fetch: https://www.reddit",
    "web_fetch: https://reddit",
    "web_fetch: https://old.reddit",
    "web_fetch: https://www.google",
    "web_fetch: https://google",
    "web_fetch: https://www.yelp",
    "web_fetch: https://yelp",
]

# A genuinely-grounded local-lookup answer tends to name a concrete business and
# a locating detail (a neighborhood, street, rating, or hours).
_BRAVE_ROUTE = {
    "require": ["load_skill: web-search"],
    "require_any": ["api.search.brave.com", "curl"],
}

TASKS: list[dict] = [
    # ════════════════════════════════════════════════════════════════════════
    # 1. Regression tripwire — the core routing path (the issue's literal scenario)
    # ════════════════════════════════════════════════════════════════════════
    {
        "id": "WS1",
        "name": "salon-route-brave",
        "intent": (
            "Core routing: the issue's canonical consumer lookup must load the "
            "web-search skill and run a Brave search, not scrape Google/Yelp."
        ),
        "expected": (
            "Loads web-search from the catalog AND runs a curl against the Brave "
            "API."
        ),
        "tags": ["regression", "web-search", "routing"],
        "prompt": "Find me a well-reviewed hair salon around San Mateo, CA.",
        "max_loops": 12,
        "grader": "action_used",
        "spec": dict(_BRAVE_ROUTE),
        # The whole point of the PR: reach Brave without the 5-6-call rediscovery
        # loop. A correct answer that burned it still scores PASS-SLOW.
        "budget": {"max_actions": 6},
    },

    # ════════════════════════════════════════════════════════════════════════
    # 2. Failure-mode — the central fix: don't raw-scrape the blocked sites
    # ════════════════════════════════════════════════════════════════════════
    {
        "id": "WS2",
        "name": "no-raw-scrape-blocked-sites",
        "intent": (
            "Method adherence: a local-business lookup must route through Brave "
            "and must NOT web_fetch/raw-curl Google/Yelp/Reddit — the bot-blocked "
            "rediscovery loop the PR removes."
        ),
        "expected": (
            "Loads web-search + curls Brave, and does NOT web_fetch or curl "
            "Google-search / Yelp / Reddit."
        ),
        "tags": ["failure-mode", "web-search", "no-rediscovery", "method"],
        "prompt": (
            "I'm in downtown Palo Alto tonight — find me a highly-rated sushi "
            "restaurant nearby and tell me its hours."
        ),
        "max_loops": 14,
        "grader": "action_used",
        "spec": {**_BRAVE_ROUTE, "forbid": list(_BLOCKED_SCRAPE)},
        "budget": {"max_actions": 8},  # search + maybe one non-blocked fetch
    },

    # ════════════════════════════════════════════════════════════════════════
    # 2. Failure-mode — answer-from-memory: a current local listing must be fetched
    # ════════════════════════════════════════════════════════════════════════
    {
        "id": "WS3",
        "name": "no-memory-local-listing",
        "intent": (
            "Answer-from-memory probe: current local-business specifics (which "
            "places exist, their ratings/hours) can't come from training — they "
            "must be searched, and the answer must read as grounded in real "
            "results, not plausible-sounding invention."
        ),
        "expected": (
            "Routes through web-search + Brave, and the answer names concrete, "
            "real-seeming businesses with locating detail rather than generic "
            "could-have-been-from-memory prose."
        ),
        "tags": ["failure-mode", "web-search", "answer-from-memory", "grounding"],
        "prompt": (
            "Recommend a couple of well-reviewed coffee shops near Union Square "
            "in San Francisco. Base it on real current listings, not general "
            "impressions."
        ),
        "max_loops": 14,
        "grader": "composite",
        "spec": {
            "all": [
                {"label": "searched-not-recited", "grader": "action_used",
                 "spec": {**_BRAVE_ROUTE, "forbid": list(_BLOCKED_SCRAPE)}},
                {"label": "grounded-not-generic", "grader": "llm_judge",
                 "spec": {"rubric": (
                     "The user asked for a couple of well-reviewed coffee shops "
                     "near Union Square in San Francisco, explicitly wanting an "
                     "answer based on real current listings rather than general "
                     "impressions.\n"
                     "PASS if the response names specific, plausibly-real coffee "
                     "shops with concrete locating detail (a street/cross-street, "
                     "neighborhood, rating, or hours) that reads as drawn from "
                     "actual search results.\n"
                     "FAIL if it gives only generic advice with no named places, "
                     "names places with no locating/rating detail at all, or "
                     "states it could not retrieve listings yet still asserts "
                     "recommendations as fact."
                 )}},
            ]
        },
        "budget": {"max_actions": 8},
    },

    # ════════════════════════════════════════════════════════════════════════
    # 3. Composition — search → fetch the RIGHT (non-blocked) URL → synthesize
    # ════════════════════════════════════════════════════════════════════════
    {
        "id": "WS4",
        "name": "search-then-fetch-own-site",
        "intent": (
            "Composition seam: the documented pattern is Brave FIRST, then "
            "web_fetch only specific non-blocked result URLs (a business's own "
            "site) — never opening with a fetch of Yelp/Google. Tests that the "
            "search→fetch handoff picks an allowed URL."
        ),
        "expected": (
            "Searches Brave, then (if it fetches at all) fetches a non-blocked "
            "result URL, and returns concrete menu/hours detail — never opening "
            "with a Yelp/Google scrape."
        ),
        "tags": ["composition", "web-search", "search-then-fetch"],
        "prompt": (
            "Find the brunch hours and a few menu highlights for a popular "
            "brunch spot in Hayes Valley, San Francisco."
        ),
        "max_loops": 16,
        "grader": "composite",
        "spec": {
            "all": [
                {"label": "brave-first-not-scrape", "grader": "action_used",
                 # Search ran; no blocked-site scrape opened the lookup. A
                 # web_fetch of the SPOT'S OWN site is allowed (not forbidden).
                 "spec": {**_BRAVE_ROUTE, "forbid": list(_BLOCKED_SCRAPE)}},
                {"label": "concrete-synthesis", "grader": "llm_judge",
                 "spec": {"rubric": (
                     "The user asked for the brunch hours and a few menu "
                     "highlights of a popular brunch spot in Hayes Valley, San "
                     "Francisco.\n"
                     "PASS if the response names a specific brunch spot and gives "
                     "concrete detail — actual brunch hours and/or named menu "
                     "items — that reads as drawn from search results or the "
                     "venue's own page.\n"
                     "FAIL if it names no specific venue, gives no hours or menu "
                     "items, or offers only generic 'look for a place that...' "
                     "advice."
                 )}},
            ]
        },
        "budget": {"max_actions": 10},
    },

    # ════════════════════════════════════════════════════════════════════════
    # 2. Failure-mode — capability-triggered routing (the tool is never named)
    # ════════════════════════════════════════════════════════════════════════
    {
        "id": "WS5",
        "name": "route-by-capability",
        "intent": (
            "Capability-first routing: a local-business ask that never says "
            "'search', 'Brave', or 'web' should still route to web-search — the "
            "manifest description triggers on the consumer-lookup capability, not "
            "a keyword."
        ),
        "expected": (
            "Auto-routes to web-search + Brave for a 'where should I...' local "
            "lookup with no search-tool keyword in the prompt, and skips the "
            "blocked-site scrape."
        ),
        "tags": ["failure-mode", "web-search", "routing", "capability-trigger"],
        "prompt": (
            "Where should I take my parents for a nice anniversary dinner in "
            "Healdsburg next weekend? Somewhere people rate highly."
        ),
        "max_loops": 14,
        "grader": "action_used",
        "spec": {**_BRAVE_ROUTE, "forbid": list(_BLOCKED_SCRAPE)},
        "budget": {"max_actions": 8},
    },
]
