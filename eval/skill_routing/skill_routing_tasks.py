"""Skill-routing & adherence eval suite — tasks as data.

Scope: the three live-data skills whose descriptions were reframed to lead with
the *capability* (hard, sourced numbers) rather than the *source brand* —
`yfinance`, `fred`, `polymarket`. The suite answers two questions the reframe
was meant to move:

  1. ROUTING — given a use case that REQUIRES the skill, does the agent load it
     and call its driver, instead of answering from memory / training data?
     Graded with `action_used` (the routing contract IS the action: the
     skill loaded from the catalog AND the driver ran). The path to a driver
     (`yfin.py`, `fred.py`, `polymarket.py`) is documented nowhere but the
     skill, so running it without `load_skill` means the agent globbed the
     catalog by hand; a bare-memory answer does neither.

  2. ADHERENCE — given the intent, does the answer follow the skill's rules?
     Cite the freshness stamp (`as_of` / observation date / `fetched_at`), use
     the SMALLEST subcommand (no `info`-by-reflex), report YoY inflation rather
     than the raw CPI index level, frame prediction-market numbers as priced
     probabilities (not certainties). Graded with `regex_present` /
     `numeric_tolerance` (anchored to the SAME driver the agent uses) /
     `llm_judge`.

Run under a persona that allowlists all three skills (`finance` does):

    CURUNIR_PERSONA=finance python run.py                       # SUT, one shell
    python eval/skill-routing/run_skill_routing_evals.py        # this suite, another

Routing graders (`action_used`) check that the call was ATTEMPTED, so they pass
without API keys. Adherence checks that read live values (YF4's anchored P/E,
the citation/format probes) need the skill's network/keys to be present in BOTH
the SUT's env and this runner's env. `llm_judge` needs ANTHROPIC_API_KEY (or
JUDGE_MODEL). See README.md.

Tasks are organised by the four eval-design sources:
  1. Regression tripwires  — one easy routing task per skill
  2. Failure-mode probes   — answer-from-memory, info-by-reflex, the CPI trap,
                             capability-triggered routing for the brand we hid
  3. Composition points    — cross-skill seams (P/E vs treasury yield)
  4. Grader-first          — applied as a filter (every task has a crisp grader)

Each task dict:
    id       — stable short id (YF*/FR*/PM*)
    name     — kebab slug
    intent   — what failure this catches (report only)
    expected — the contract in one line (report only)
    tags     — source tag + skill tag + symptom tag (for --tag filtering)
    prompt   — the exact user message sent to the agent
    max_loops— hard tool-call stop for this task
    grader   — dispatch key into eval.harness.graders.GRADERS
    spec     — grader config (anchors recomputed at grade time)
    budget   — optional process budget; a correct run over it scores PASS-SLOW
"""

# Date-ish freshness stamps the skills are required to cite alongside any number.
_DATE = r"20\d{2}-\d{2}-\d{2}|\b20\d{2}\b"
_PCT = r"\d{1,3}(?:\.\d+)?\s?%"

TASKS: list[dict] = [
    # ════════════════════════════════════════════════════════════════════════
    # YFINANCE — equity market data
    # ════════════════════════════════════════════════════════════════════════

    # ── 1. Regression tripwire: bare price must hit the live driver ──────────
    {
        "id": "YF1",
        "name": "yfinance-route-price",
        "intent": "Core routing: a bare price request must load yfinance and call yfin.py, not answer from memory.",
        "expected": "Loads yfinance from the catalog AND runs yfin.py in ~1 data call; no orchestration.",
        "tags": ["regression", "yfinance", "routing"],
        "prompt": "What is Apple (AAPL) trading at right now? Just the price.",
        "max_loops": 5,
        "grader": "action_used",
        # require BOTH: load_skill proves catalog routing; yfin.py proves the
        # real data call. A memory answer produces neither.
        "spec": {"require": ["load_skill: yfinance", "yfin.py"]},
        "budget": {"max_actions": 4},  # a bare price is ~1 data call
    },

    # ── 2. Failure-mode: a known fundamental the model might recite ──────────
    {
        "id": "YF2",
        "name": "yfinance-no-memory-fundamental",
        "intent": "Answer-from-memory probe: a well-known fundamental must be FETCHED live with a freshness stamp, not recited from training.",
        "expected": "Routes through yfin.py (financials) for MSFT revenue AND cites an as-of / fiscal date.",
        "tags": ["failure-mode", "yfinance", "answer-from-memory", "citation"],
        "prompt": (
            "What was Microsoft's (MSFT) total revenue in its most recent "
            "fiscal year? Give the figure with the as-of date."
        ),
        "max_loops": 7,
        "grader": "composite",
        "spec": {
            "all": [
                {"label": "fetched-not-recited", "grader": "action_used",
                 "spec": {"require": ["load_skill: yfinance", "yfin.py"]}},
                {"label": "cites-a-date", "grader": "regex_present",
                 "spec": {"require": [_DATE]}},
            ]
        },
    },

    # ── 2. Failure-mode: don't escalate to the context-flooding `info` ───────
    {
        "id": "YF3",
        "name": "yfinance-smallest-subcommand",
        "intent": "Adherence: a bare price must use the SMALLEST subcommand (quote), not the 200-field `info` escape hatch by reflex.",
        "expected": "Calls `yfin.py quote` and does NOT call `yfin.py info` / `financials` for a single price.",
        "tags": ["failure-mode", "yfinance", "smallest-subcommand"],
        "prompt": "What's Tesla (TSLA) trading at right now? Only the current price.",
        "max_loops": 5,
        "grader": "action_used",
        "spec": {
            "require": ["yfin.py quote"],
            "forbid": ["yfin.py info", "yfin.py financials"],
        },
        "budget": {"max_actions": 4},
    },

    # ── 1./4. Regression + anchored adherence: P/E within tolerance, sourced ─
    {
        "id": "YF4",
        "name": "yfinance-pe-anchored",
        "intent": "Adherence to ground truth: the reported trailing P/E must match the live yfin.py value, and be sourced via the skill.",
        "expected": "Reports NVDA trailing P/E within 8% of the live yfin.py value, fetched via the skill.",
        "tags": ["regression", "yfinance", "citation", "anchored"],
        "prompt": "What is NVIDIA's (NVDA) trailing P/E ratio? Just the number, sourced.",
        "max_loops": 6,
        "grader": "composite",
        "spec": {
            "all": [
                {"label": "used-yfin", "grader": "action_used",
                 "spec": {"require": ["load_skill: yfinance", "yfin.py"]}},
                {"label": "pe-matches-live", "grader": "numeric_tolerance",
                 "spec": {
                     "tolerance_pct": 8,  # price moves between anchor and answer
                     "anchor": {
                         "cmd": ["python", "skills/yfinance/yfin.py", "multiples", "NVDA"],
                         "json_path": "trailing_pe",
                     },
                 }},
            ]
        },
    },

    # ── 3. Composition: two fetches must chain into one comparison ───────────
    {
        "id": "YF5",
        "name": "yfinance-two-ticker-compare",
        "intent": "Composition seam: comparing two names forces two data calls to chain into a single ranked answer.",
        "expected": "Fetches LLY and NVO via yfin.py, names both, and states which forward multiple is higher.",
        "tags": ["composition", "yfinance"],
        "prompt": (
            "Compare Eli Lilly (LLY) and Novo Nordisk (NVO) on forward P/E — "
            "which one is more expensive?"
        ),
        "max_loops": 10,
        "grader": "composite",
        "spec": {
            "all": [
                {"label": "fetched-via-skill", "grader": "action_used",
                 "spec": {"require": ["load_skill: yfinance", "yfin.py"]}},
                {"label": "names-both-and-ranks", "grader": "regex_present",
                 "spec": {"require": [r"\bLLY\b", r"\bNVO\b",
                                      r"more expensive|richer|cheaper|higher|lower"]}},
            ]
        },
    },

    # ════════════════════════════════════════════════════════════════════════
    # FRED — US macroeconomic data
    # ════════════════════════════════════════════════════════════════════════

    # ── 1. Regression tripwire: a rate lookup must hit the live driver ───────
    {
        "id": "FR1",
        "name": "fred-route-treasury",
        "intent": "Core routing: a treasury-yield lookup must load fred and call fred.py, not answer from memory.",
        "expected": "Loads fred from the catalog AND runs fred.py (e.g. DGS10).",
        "tags": ["regression", "fred", "routing"],
        "prompt": "What is the current US 10-year Treasury yield?",
        "max_loops": 6,
        "grader": "action_used",
        "spec": {"require": ["load_skill: fred", "fred.py"]},
        "budget": {"max_actions": 5},
    },

    # ── 2. Failure-mode: answer-from-memory + citation adherence ─────────────
    {
        "id": "FR2",
        "name": "fred-no-memory-cited",
        "intent": "Answer-from-memory probe + citation rule: a macro stat must be fetched and cited with series ID + observation date.",
        "expected": "Routes through fred.py AND cites the UNRATE series, an observation date, and a percentage.",
        "tags": ["failure-mode", "fred", "answer-from-memory", "citation"],
        "prompt": (
            "What is the most recent US unemployment rate? Cite the FRED series "
            "ID and the observation date."
        ),
        "max_loops": 7,
        "grader": "composite",
        "spec": {
            "all": [
                {"label": "fetched-not-recited", "grader": "action_used",
                 "spec": {"require": ["load_skill: fred", "fred.py"]}},
                {"label": "cites-series-date-pct", "grader": "regex_present",
                 "spec": {"require": [r"UNRATE", _DATE, _PCT]}},
            ]
        },
    },

    # ── 2. Failure-mode: the CPI-level-vs-inflation trap (a documented mistake)
    {
        "id": "FR3",
        "name": "fred-cpi-not-index-level",
        "intent": "Adherence: must report YoY inflation as a percentage, NOT quote the raw CPI index level as if it were the inflation rate.",
        "expected": "Fetches from FRED and reports inflation as a ~single-digit %, not the ~300 CPI index level.",
        "tags": ["failure-mode", "fred", "cpi-trap"],
        "prompt": "What is the current US inflation rate (CPI, year-over-year)?",
        "max_loops": 8,
        "grader": "composite",
        "spec": {
            "all": [
                {"label": "fetched-from-fred", "grader": "action_used",
                 "spec": {"require": ["fred.py"]}},
                {"label": "yoy-not-index-level", "grader": "llm_judge",
                 "spec": {"rubric": (
                     "The user asked for the US year-over-year CPI inflation "
                     "rate.\n"
                     "PASS if the response reports inflation as a year-over-year "
                     "percentage change (a small number, typically between about "
                     "1% and 6%).\n"
                     "FAIL if it instead quotes the raw CPI INDEX LEVEL (a value "
                     "around 250-350) as if that were the inflation rate, or "
                     "otherwise confuses the index level with the rate of change."
                 )}},
            ]
        },
    },

    # ── 3. Composition: fred + yfinance seam (earnings yield vs treasury) ────
    {
        "id": "FR4",
        "name": "fred-yfinance-earnings-yield-seam",
        "intent": "Cross-skill seam: comparing an equity's earnings yield to the risk-free rate forces BOTH yfin.py and fred.py — the classic dropped handoff.",
        "expected": "Pulls NVDA's P/E via yfin.py AND the 10y via fred.py, then states above/below with both rates.",
        "tags": ["composition", "fred", "yfinance", "handoff"],
        "prompt": (
            "Is NVIDIA's (NVDA) earnings yield (the inverse of its P/E) "
            "currently above or below the US 10-year Treasury yield? Show both "
            "numbers."
        ),
        "max_loops": 12,
        "grader": "composite",
        "spec": {
            "all": [
                {"label": "used-both-skills", "grader": "action_used",
                 "spec": {"require": ["yfin.py", "fred.py"]}},
                {"label": "states-comparison", "grader": "regex_present",
                 "spec": {"require": [r"above|below|higher|lower", _PCT]}},
            ]
        },
    },

    # ════════════════════════════════════════════════════════════════════════
    # POLYMARKET — prediction-market probabilities (un-hidden so it can route)
    # ════════════════════════════════════════════════════════════════════════

    # ── 1. Regression tripwire: brand-named request routes to the driver ─────
    {
        "id": "PM1",
        "name": "polymarket-route-named",
        "intent": "Core routing: an explicitly brand-named request must load polymarket and call polymarket.py.",
        "expected": "Loads polymarket from the catalog AND runs polymarket.py.",
        "tags": ["regression", "polymarket", "routing"],
        "prompt": (
            "What is Polymarket showing right now for which party is favored to "
            "control the US House after the 2026 midterms?"
        ),
        "max_loops": 7,
        "grader": "action_used",
        "spec": {"require": ["load_skill: polymarket", "polymarket.py"]},
        "budget": {"max_actions": 6},
    },

    # ── 2. Failure-mode: CAPABILITY-triggered routing (no brand named) ───────
    # This is the central test of the reframe + un-hide: a prediction-market
    # question phrased purely by capability ("market-implied probability")
    # should now route to polymarket on its own.
    {
        "id": "PM2",
        "name": "polymarket-route-by-capability",
        "intent": "The reframe+un-hide payoff: a betting-market question that never names Polymarket must still auto-route to it (capability-first description in the catalog).",
        "expected": "Auto-routes to polymarket (load_skill / polymarket.py) for an implied-probability question, without the brand being named.",
        "tags": ["failure-mode", "polymarket", "routing", "capability-trigger"],
        "prompt": (
            "What's the market-implied probability of a Fed interest-rate cut "
            "at the next FOMC meeting?"
        ),
        "max_loops": 8,
        "grader": "action_used",
        "spec": {"require_any": ["load_skill: polymarket", "polymarket.py"]},
    },

    # ── 2./4. Failure-mode: adherence — cite prob + URL + snapshot, framed as
    #          a PRICED probability, not a certainty ────────────────────────
    {
        "id": "PM3",
        "name": "polymarket-priced-probability-cited",
        "intent": "Adherence: a prediction-market answer must cite the market URL, the implied probability, and the snapshot time, and frame it as a priced probability — not a definite forecast.",
        "expected": "Fetches via polymarket.py; cites a polymarket.com market + implied % + fetched-at; frames it as odds that can move, not certainty.",
        "tags": ["failure-mode", "polymarket", "citation", "priced-probability"],
        "prompt": (
            "Using prediction markets, what are the current odds of a US "
            "recession being officially declared this year? Cite the specific "
            "market, the implied probability, and the snapshot time."
        ),
        "max_loops": 9,
        "grader": "composite",
        "spec": {
            "all": [
                {"label": "fetched-via-skill", "grader": "action_used",
                 "spec": {"require": ["polymarket.py"]}},
                {"label": "cites-market-prob-time", "grader": "regex_present",
                 "spec": {"require": [r"polymarket\.com",
                                      r"%|0\.\d|\bprobab|\bodds\b",
                                      r"20\d{2}-\d{2}-\d{2}|UTC|\bas of\b|fetched"]}},
                {"label": "priced-not-certain", "grader": "llm_judge",
                 "spec": {"rubric": (
                     "The user asked for prediction-market odds of a recession "
                     "being declared this year.\n"
                     "PASS if the response presents the number as a MARKET-IMPLIED "
                     "probability / odds — something priced by bettors that can "
                     "move — e.g. hedged language like 'the market implies ~X%', "
                     "'currently priced at'.\n"
                     "FAIL if it states the outcome as a certainty or presents "
                     "the probability as a definitive forecast of what WILL "
                     "happen, with no sense that it is a live, movable market price."
                 )}},
            ]
        },
    },
]
