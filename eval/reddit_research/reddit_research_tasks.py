"""Reddit-research eval suite — tasks as data.

Scope: the `reddit-research` skill, a two-step pipeline that (1) discovers
Reddit posts via Brave Search (`site:reddit.com`) or Reddit's own `search.json`,
then (2) extracts full post + comment content by appending `.json` to a Reddit
URL and parsing it with `curl` + `jq`. The skill exists because Reddit blocks
headless browsers — so the *method* (curl the public JSON API, never a browser
or `web_fetch` on a `.json` endpoint) is itself part of the contract, not just
the answer.

The suite answers the two questions worth grading for this skill:

  1. ROUTING — given a request that REQUIRES live Reddit community voice (which
     cannot be answered from training data), does the agent load the skill and
     actually fetch, instead of fabricating sentiment from memory? Graded with
     `action_used` — the routing contract IS the action: the skill loaded from
     the catalog AND a `curl` to Reddit/Brave ran. A bare-memory answer does
     neither.

  2. METHOD ADHERENCE — does extraction follow the skill's documented method
     (curl the JSON API), NOT the documented mistakes (a headless browser, or
     `web_fetch`/trafilatura pointed at a `.json` URL, which return mangled
     output)? Graded with `action_used` (require `curl`, forbid a `web_fetch`
     of Reddit) + `llm_judge` for whether the synthesis is grounded in fetched
     discussion rather than generic.

Run under a persona that allowlists `reddit-research` (`marketing` and `finance`
both do):

    CURUNIR_PERSONA=marketing python run.py                       # SUT, one shell
    python eval/reddit_research/run_reddit_research_evals.py      # this suite, another

Routing graders (`action_used`) check the call was ATTEMPTED, so they pass even
without keys — but the prompts genuinely need `BRAVE_API_KEY` + network for the
agent to produce a grounded answer, and the `llm_judge` checks need a judge key
(see README). Reddit content is live and non-deterministic, so NO task anchors
an exact value — they grade routing, method, and grounding, all of which stay
valid as threads come and go.

Tasks are organised by the four eval-design sources:
  1. Regression tripwires  — the core routing path must never break (RR1)
  2. Failure-mode probes    — answer-from-memory (RR2), wrong extraction tool
                              (RR3), capability-triggered routing with the
                              source un-named (RR5)
  3. Composition points     — discovery → extraction → synthesis seam (RR4)
  4. Grader-first           — applied as a filter (every task has a crisp grader)

Each task dict:
    id       — stable short id (RR*)
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
early in the command — `curl`, `reddit.com`, `brave` (all near the front of the
skill's commands) match reliably; the `User-Agent` header and the trailing
`.json` do NOT (they sit past the cutoff), so they are deliberately not graded.
`load_skill: reddit-research` and a full `web_fetch: <url>` are NOT truncated.
"""

# Reddit markers a genuinely-grounded answer tends to carry: a subreddit handle
# (r/foo) or a reddit.com link.
_REDDIT_MARKER = r"r/\w+|reddit\.com"

TASKS: list[dict] = [
    # ════════════════════════════════════════════════════════════════════════
    # 1. Regression tripwire — the core routing path must never break
    # ════════════════════════════════════════════════════════════════════════
    {
        "id": "RR1",
        "name": "reddit-route-sentiment",
        "intent": (
            "Core routing: an explicit Reddit-sentiment request must load the "
            "skill and actually curl Reddit, not answer from memory."
        ),
        "expected": (
            "Loads reddit-research from the catalog AND runs a curl against "
            "Reddit/Brave."
        ),
        "tags": ["regression", "reddit-research", "routing"],
        "prompt": (
            "What are people on Reddit saying about the Logitech MX Master 3S "
            "mouse? Give me the gist of the community sentiment."
        ),
        "max_loops": 14,
        "grader": "action_used",
        # load_skill proves catalog routing; curl proves the skill's actual
        # data-fetch method ran (vs a fabricated answer that does neither).
        "spec": {
            "require": ["load_skill: reddit-research"],
            "require_any": ["curl", "reddit.com", "brave"],
        },
        "budget": {"max_actions": 12},  # discover a few posts + extract them
    },

    # ════════════════════════════════════════════════════════════════════════
    # 2. Failure-mode — answer-from-memory: sentiment MUST be fetched live
    # ════════════════════════════════════════════════════════════════════════
    {
        "id": "RR2",
        "name": "reddit-no-memory-sentiment",
        "intent": (
            "Answer-from-memory probe: current community opinion cannot come "
            "from training — it must be fetched, and the answer must show its "
            "Reddit grounding (subreddit / thread), not generic prose."
        ),
        "expected": (
            "Routes through the skill + curl, the answer cites Reddit markers "
            "(r/... or a reddit.com link), and reads as grounded in actual "
            "fetched discussion rather than plausible-sounding generalities."
        ),
        "tags": ["failure-mode", "reddit-research", "answer-from-memory", "grounding"],
        "prompt": (
            "I'm evaluating Obsidian as a note-taking app. What do actual "
            "Reddit users complain about most? Base it on real threads, not "
            "general impressions."
        ),
        "max_loops": 16,
        "grader": "composite",
        "spec": {
            "all": [
                {"label": "fetched-not-recited", "grader": "action_used",
                 "spec": {"require": ["load_skill: reddit-research"],
                          "require_any": ["curl", "reddit.com"]}},
                {"label": "cites-reddit-markers", "grader": "regex_present",
                 "spec": {"require": [_REDDIT_MARKER]}},
                {"label": "grounded-not-generic", "grader": "llm_judge",
                 "spec": {"rubric": (
                     "The user asked what actual Reddit users complain about "
                     "regarding the Obsidian note-taking app, explicitly wanting "
                     "an answer based on real threads rather than general "
                     "impressions.\n"
                     "PASS if the response reads as grounded in specific fetched "
                     "Reddit discussion: it names subreddits or links/quotes "
                     "specific threads or comments, and the complaints are "
                     "concrete (e.g. specific features, sync, pricing, mobile) "
                     "rather than vague boilerplate.\n"
                     "FAIL if it gives only generic, could-have-been-written-"
                     "from-memory commentary with no sign of having read actual "
                     "Reddit content, or if it states it could not retrieve "
                     "anything yet still asserts complaints as fact."
                 )}},
            ]
        },
    },

    # ════════════════════════════════════════════════════════════════════════
    # 2. Failure-mode — wrong extraction tool (the skill's whole reason to exist)
    # ════════════════════════════════════════════════════════════════════════
    {
        "id": "RR3",
        "name": "reddit-curl-not-webfetch",
        "intent": (
            "Method adherence: extraction must use the documented curl + JSON-"
            "API method, NOT a headless browser or web_fetch/trafilatura on a "
            ".json URL (both return mangled output — the documented mistakes)."
        ),
        "expected": (
            "Loads the skill, extracts via curl, and does NOT web_fetch a "
            "Reddit URL."
        ),
        "tags": ["failure-mode", "reddit-research", "wrong-tool", "method"],
        "prompt": (
            "Find the actual Reddit discussion threads in r/buildapc about RAM "
            "compatibility problems and pull out what the real complaints are. "
            "I want the content of the threads, not just links."
        ),
        "max_loops": 16,
        "grader": "action_used",
        "spec": {
            # curl is the skill's extraction method; the contract is that the
            # JSON API is hit with curl and Reddit is NOT scraped via web_fetch.
            "require": ["load_skill: reddit-research"],
            "require_any": ["curl", "reddit.com", "brave"],
            "forbid": ["web_fetch: https://www.reddit",
                       "web_fetch: https://reddit",
                       "web_fetch: https://old.reddit"],
        },
        "budget": {"max_actions": 14},
    },

    # ════════════════════════════════════════════════════════════════════════
    # 3. Composition — discovery → extraction → synthesis seam
    # ════════════════════════════════════════════════════════════════════════
    {
        "id": "RR4",
        "name": "reddit-research-synthesis",
        "intent": (
            "Composition seam: a product-research ask forces discovery → "
            "extraction → synthesis to chain into a structured, grounded set of "
            "pain points — the skill working end-to-end, not in isolation."
        ),
        "expected": (
            "Discovers + extracts via the skill, then returns the top recurring "
            "complaints, each tied to real Reddit discussion."
        ),
        "tags": ["composition", "reddit-research", "synthesis"],
        "prompt": (
            "I'm doing competitive research on the Notion app. Pull together "
            "what real users say on Reddit and give me the top 3 recurring "
            "complaints, each backed by a specific thread or subreddit."
        ),
        "max_loops": 18,
        "grader": "composite",
        "spec": {
            "all": [
                {"label": "fetched-via-skill", "grader": "action_used",
                 "spec": {"require": ["load_skill: reddit-research"],
                          "require_any": ["curl", "reddit.com"]}},
                {"label": "structured-grounded-synthesis", "grader": "llm_judge",
                 "spec": {"rubric": (
                     "The user asked for the top 3 recurring complaints about "
                     "the Notion app from real Reddit users, each backed by a "
                     "specific thread or subreddit.\n"
                     "PASS if the response presents roughly three distinct, "
                     "concrete complaints AND each is tied to identifiable "
                     "Reddit grounding (a named subreddit, a linked/quoted "
                     "thread, or a paraphrased real comment).\n"
                     "FAIL if it gives fewer than two distinct complaints, the "
                     "complaints are generic boilerplate with no Reddit "
                     "grounding, or it never actually surfaces Reddit content."
                 )}},
            ]
        },
        "budget": {"max_actions": 16},
    },

    # ════════════════════════════════════════════════════════════════════════
    # 2. Failure-mode — capability-triggered routing (source un-named)
    # ════════════════════════════════════════════════════════════════════════
    {
        "id": "RR5",
        "name": "reddit-route-by-capability",
        "intent": (
            "Capability-first routing: a request for real community/user voice "
            "that never says 'Reddit' should still route to reddit-research "
            "(the description triggers on the capability, not just the brand)."
        ),
        "expected": (
            "Auto-routes to reddit-research for a 'what are real users "
            "complaining about' ask without the word 'Reddit' in the prompt."
        ),
        "tags": ["failure-mode", "reddit-research", "routing", "capability-trigger"],
        "prompt": (
            "I'm researching the Rivian R1S as a competitor. What are real "
            "owners actually complaining about in online communities? I want "
            "genuine user voices, not reviews or marketing copy."
        ),
        "max_loops": 16,
        "grader": "action_used",
        "spec": {"require": ["load_skill: reddit-research"]},
    },
]
