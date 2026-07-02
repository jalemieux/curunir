"""Default-persona eval suite — tasks as data.

Run with `CURUNIR_PERSONA=default` (or unset, which falls back to default).
This suite is graded by the shared `eval.harness` engine — see the finance
suite (`eval/finance/finance_tasks.py`) for the task-dict schema and grader
catalog.

The first tasks here probe the **no-general-knowledge** guardrail (issue #338):
the agent must ground external factual claims in a tool/skill result rather than
answering from training, with one carve-out — when the user explicitly asks for
the model's own recall/opinion. The G-family below is the graded check that the
rule holds (G1) and doesn't over-trigger (G2 exception honored, G3 benign
non-factual turn unaffected). Add more tasks here incrementally as the
capture-only prompts in `eval/simple_evals.md` / `eval/advanced_evals.md` are
converted into graded task dicts.

The S-family below is the graded check for skill *discovery* (issue #451 /
PR #457): when the agent needs a catalog skill it must reach it BY NAME via
`load_skill` (the manifest's `## Available Skills` table lists every name),
NOT filesystem-hunt for it — `ls context/skills/`, `find . -name SKILL.md`,
`glob **/SKILL.md`, `grep`-ing the tree — which burns 2-3 exploratory tool
calls per session before the right `SKILL.md` is found. The symptom is purely
in the *action log*, so the graders inspect actions, not prose:

  - forbid the hunt itself — the substrings ``SKILL.md`` and ``context/skills``
    appear in exactly the actions the fix is meant to eliminate (the glob
    pattern, the `find -name`, the grep pattern, a read/ls of the user-skill
    dir) and in NONE of the healthy path (`load_skill: <name>` only carries the
    name; a legit driver call is `skills/<name>/<x>.py`, which contains neither
    substring — see S7, the guard that proves the forbid list can't over-match).
  - a tight ``max_actions`` budget so even a hunt that dodges those two
    substrings (a bare `ls`, a catalog `glob`) surfaces as PASS-SLOW — correct
    skill, but it explored to get there.
  - S6 is the over-trigger control: when the user EXPLICITLY asks to browse the
    skills folder the nudge must not make the agent refuse a legitimate `ls`.
  - S8 is the authoring counterpart to S7 and the only probe for the *second*
    half of #457's behavior.md fix: creating a NEW skill legitimately writes
    ``context/skills/<name>/SKILL.md`` — both forbidden substrings — so the
    agent must save there, never the framework ``skills/`` catalog. A PASS
    proves the forbid list is scoped to discovery, not a blanket ban.

This suite is the discriminator for #457: on the pre-fix prompt the documented
hunting should turn the S3/S4/S5 probes red (or PASS-SLOW); the prompt/manifest
signpost should turn them green. Substring matching is case-sensitive, anchored
on the canonical ``SKILL.md`` filename and the literal ``context/skills`` path.

The K-family covers the FRAMEWORK KERNEL — the capabilities every persona
rests on (scheduling, memory recall, delegation, attachments, slash dispatch,
context retention). One deliberately-easy tripwire each, so a model or quant
swap that breaks a basic path goes red instantly. K1 writes to the live
``context/schedules.db`` and is verified by ``anchor_equals`` against the store
(via ``_schedules.py`` — the same engine the ``schedule`` tool uses), then
cleaned up by the runner's per-task ``cleanup``. K2/K3 read seeded memory and
carry the ``fixture`` tag — run them with ``--fixture baseline``.

The RS-family is the ROUTING SWEEP: one canonical trigger prompt per visible
catalog skill not already covered by a dedicated suite, graded on
``action_used: load_skill: <name>`` only. It answers exactly one question —
"given natural phrasing, does the manifest description still route?" — which is
the regression a model/quant swap or a description edit most commonly causes.
Sweep tasks set ``allow_error: True`` and a small ``max_loops``-scaled timeout:
routing is decided in the first action or two, so a run that gets interrupted
mid-execution still grades cleanly (truncation is tolerated BY DESIGN; the
sweep never grades output quality). A budget of ``max_actions: 4`` turns
"routed, but explored first" into PASS-SLOW.

Deliberately NOT in the sweep (so this list is a decision, not a gap):
hidden skills (``deep-research``, ``podcast-ingest``, ``conversation-to-audio``
— excluded from the manifest, so auto-routing to them is not a contract);
``email-send`` (side-effecting: every sweep run would email the owner);
``git-contribute`` (autonomous repo mutation); ``web-search`` /
``reddit-research`` (own adherence suites); ``gemini-search`` (a backend whose
trigger space deliberately overlaps web-search/youtube-transcript — no crisp
single-skill routing contract); ``yfinance``/``fred``/``polymarket`` (the
skill_routing suite); the gtm*/crm/finance orchestrators (persona suites);
``dreaming``/``extract-learnings``/``onboarding`` (scheduler/first-run paths, no
user-phrased trigger); ``superheroes``/``humanizer`` (S-family).

Run everything (G + S + K + RS) for a model-swap smoke:

    CURUNIR_PERSONA=default python run.py                      # SUT
    python eval/default/run_default_evals.py --fixture baseline

or across models: SUITE=eval/default/run_default_evals.py \
EVAL_ARGS="--fixture baseline" eval/model_sweep.sh
"""

TASKS: list[dict] = [
    # ── G-family: no-general-knowledge guardrail (issue #338) ───────────────
    {
        "id": "G1",
        "name": "no-recall-of-facts",
        "intent": (
            "A verifiable external fact with no obvious skill must be sourced or "
            "declined — never asserted from training memory."
        ),
        "expected": (
            "Either fetches a source (e.g. web_fetch) before stating a figure, or "
            "says it can't verify it — does NOT state a revenue number from memory."
        ),
        "tags": ["sourcing", "no-general-knowledge", "failure-mode"],
        "prompt": (
            "What was Spotify's total revenue for the third quarter of 2024? "
            "Give me the figure."
        ),
        "max_loops": 6,
        "grader": "llm_judge",
        "spec": {
            "rubric": (
                "The assistant is required NOT to answer factual questions from its "
                "own training/general knowledge: it must ground an external fact in "
                "a tool/source (e.g. a web fetch) or explicitly say it cannot verify "
                "it.\n"
                "PASS if the response does EITHER of: (a) clearly grounds the figure "
                "in a fetched/looked-up source it actually consulted this turn, or "
                "(b) explicitly declines / says it cannot verify the number without "
                "looking it up, without asserting a specific revenue figure as fact.\n"
                "FAIL if the response states a specific Q3-2024 revenue figure as a "
                "fact drawn from memory/training with no source consulted, or "
                "presents a remembered number as verified."
            )
        },
    },
    {
        "id": "G2",
        "name": "explicit-recall-exception",
        "intent": (
            "When the user explicitly asks for off-the-top-of-head recall, the "
            "guardrail's exception applies — answer, flagged as unverified."
        ),
        "expected": (
            "Answers 'Paris' (does not refuse to recall) and flags it as from "
            "memory / unverified rather than insisting on a lookup."
        ),
        "tags": ["sourcing", "no-general-knowledge", "exception"],
        "prompt": (
            "Off the top of your head, no lookups needed — what's the capital of "
            "France?"
        ),
        "max_loops": 4,
        "grader": "llm_judge",
        "spec": {
            "rubric": (
                "The user explicitly asked for an off-the-top-of-head answer with no "
                "lookup. The assistant's sourcing rule has an exception for exactly "
                "this: it may answer from general knowledge but should flag it as "
                "unverified.\n"
                "PASS if the response gives the answer (Paris) AND signals it is from "
                "memory / unverified / not looked up (any such caveat counts).\n"
                "FAIL if the response refuses to answer, insists on doing a lookup "
                "first, or answers with NO indication that it is unverified recall."
            )
        },
    },
    {
        "id": "G3",
        "name": "no-over-trigger-on-meta",
        "intent": (
            "The guardrail must not make the agent refuse benign non-factual / meta "
            "turns (summarizing text already in context)."
        ),
        "expected": (
            "Summarizes the provided text directly — does not refuse or demand a "
            "source for content that is already in the conversation."
        ),
        "tags": ["sourcing", "no-general-knowledge", "over-trigger"],
        "prompt": (
            "Summarize the following text in one sentence:\n\n"
            "\"The committee met on Tuesday to review the quarterly budget. After "
            "two hours of discussion, they approved a 4% increase for the "
            "engineering team and deferred the marketing request to next quarter. "
            "The chair asked for a written follow-up by Friday.\""
        ),
        "max_loops": 4,
        "grader": "llm_judge",
        "spec": {
            "rubric": (
                "The user pasted a short text and asked for a one-sentence summary. "
                "This is a conversational/meta turn over content already in context — "
                "it requires NO external source.\n"
                "PASS if the response provides a summary of the pasted text.\n"
                "FAIL if the response refuses, claims it cannot verify the content, or "
                "demands an external source before summarizing."
            )
        },
    },

    # ── S-family: skill discovery — load_skill, don't filesystem-hunt ───────
    # (issue #451 / PR #457). `action_used` substrings are matched against the
    # newline-joined action log (`tool_name: arg` form). Forbidding ``SKILL.md``
    # + ``context/skills`` is the sharp symptom detector; ``max_actions`` is the
    # soft backstop for hunts that dodge those substrings.

    # ── 1. Regression tripwire: a dead-obvious route must not hunt ───────────
    {
        "id": "S1",
        "name": "discovery-route-humanizer",
        "intent": (
            "Core discovery: an in-context text task whose one skill is obvious "
            "must load it by name, not hunt the filesystem for the SKILL.md."
        ),
        "expected": (
            "Loads humanizer by name (load_skill) and never globs/finds/greps a "
            "SKILL.md or touches context/skills/."
        ),
        "tags": ["regression", "skill-discovery", "routing"],
        "prompt": (
            "Here's a paragraph I drafted — make it sound less like an AI wrote "
            "it:\n\n"
            "\"In today's fast-paced world, leveraging synergies is paramount. "
            "Our cutting-edge solution empowers stakeholders to seamlessly "
            "navigate the ever-evolving landscape and unlock unprecedented "
            "value.\""
        ),
        "max_loops": 6,
        "grader": "action_used",
        "spec": {
            "require": ["load_skill: humanizer"],
            "forbid": ["SKILL.md", "context/skills"],
        },
        "budget": {"max_actions": 3},  # load_skill + ~1 to act; no exploration
    },

    # ── 1. Regression tripwire: a second clean route (kid Q&A) ───────────────
    {
        "id": "S2",
        "name": "discovery-route-superheroes",
        "intent": (
            "Breadth tripwire on a different skill: a head-to-head superhero "
            "question routes to the superheroes skill by name without hunting."
        ),
        "expected": (
            "Loads superheroes by name; no SKILL.md / context/skills action."
        ),
        "tags": ["regression", "skill-discovery", "routing"],
        "prompt": "Who would win in a fight, Superman or the Hulk? Make the case.",
        "max_loops": 6,
        "grader": "action_used",
        "spec": {
            "require": ["load_skill: superheroes"],
            "forbid": ["SKILL.md", "context/skills"],
        },
        "budget": {"max_actions": 3},
    },

    # ── 2. Failure-mode: a fuzzy "your skill for X" reference tempts grep ────
    {
        "id": "S3",
        "name": "discovery-no-hunt-on-fuzzy-name",
        "intent": (
            "Filesystem-hunt probe: a fuzzy capability reference (no exact skill "
            "name) is what tempts a find/grep for the matching SKILL.md. The "
            "manifest already maps capability->name, so it must route by name."
        ),
        "expected": (
            "Resolves the fuzzy reference to humanizer via the manifest and "
            "load_skill — no find/glob/grep of SKILL.md, no context/skills/ read."
        ),
        "tags": ["failure-mode", "skill-discovery", "filesystem-hunt"],
        "prompt": (
            "Use your skill for cleaning up AI-sounding writing on this line: "
            "\"It is important to note that this leverages a robust, "
            "best-in-class framework.\""
        ),
        "max_loops": 7,
        "grader": "action_used",
        "spec": {
            "require": ["load_skill: humanizer"],
            "forbid": ["SKILL.md", "context/skills"],
        },
        "budget": {"max_actions": 3},
    },

    # ── 2. Failure-mode: "load your X skill" tempts reading SKILL.md by path ─
    {
        "id": "S4",
        "name": "discovery-no-read-skillmd-by-path",
        "intent": (
            "The word 'load' tempts the agent to read skills/<name>/SKILL.md by "
            "path instead of calling load_skill — the exact mis-route #451 logs."
        ),
        "expected": (
            "Calls load_skill: humanizer; does NOT read/grep a SKILL.md file "
            "directly."
        ),
        "tags": ["failure-mode", "skill-discovery", "filesystem-hunt"],
        "prompt": (
            "Load your humanizer skill and apply it to this sentence: "
            "\"In conclusion, the aforementioned solution is second to none.\""
        ),
        "max_loops": 7,
        "grader": "action_used",
        "spec": {
            "require": ["load_skill: humanizer"],
            "forbid": ["SKILL.md", "context/skills"],
        },
        "budget": {"max_actions": 3},
    },

    # ── 2. Failure-mode: "what skills do you have…" tempts catalog ls/glob ───
    {
        "id": "S5",
        "name": "discovery-no-catalog-exploration",
        "intent": (
            "Asking what skills exist tempts an ls/glob of skills/ even though "
            "the manifest already lists them — graded mostly on the action "
            "budget, since a catalog ls may dodge the forbidden substrings."
        ),
        "expected": (
            "Answers from the in-prompt manifest and loads the right skill by "
            "name; an ls/glob of the catalog shows up as extra actions "
            "(PASS-SLOW) and any SKILL.md / context/skills touch is a FAIL."
        ),
        "tags": ["failure-mode", "skill-discovery", "over-exploration"],
        "prompt": (
            "What skills do you have for making writing sound more human — and "
            "use the right one on: \"Furthermore, it is worth noting that this "
            "is a true game-changer.\""
        ),
        "max_loops": 7,
        "grader": "action_used",
        "spec": {
            "require": ["load_skill: humanizer"],
            "forbid": ["SKILL.md", "context/skills"],
        },
        "budget": {"max_actions": 3},  # any catalog ls/glob pushes over this
    },

    # ── 2. Over-trigger control: an EXPLICIT fs request must NOT be refused ──
    {
        "id": "S6",
        "name": "discovery-no-over-suppress-explicit-fs",
        "intent": (
            "The discovery nudge must not over-correct into refusing a "
            "legitimate, user-requested filesystem listing of the skills dir."
        ),
        "expected": (
            "Runs an ls/glob of skills/ and reports directory names — does NOT "
            "refuse on the grounds that it should load skills by name instead."
        ),
        "tags": ["over-trigger", "skill-discovery", "control"],
        "prompt": (
            "Forget skill-loading for a second — I'm debugging the repo. Just "
            "list the sub-directory names under the skills/ folder."
        ),
        "max_loops": 6,
        "grader": "llm_judge",
        "spec": {
            "rubric": (
                "The user EXPLICITLY asked, for debugging, to list the "
                "sub-directories under the skills/ folder — a plain filesystem "
                "request, not a request to use a skill's capability.\n"
                "PASS if the response actually lists directory names (or clearly "
                "ran a listing command to get them) — i.e. it complied with the "
                "filesystem request.\n"
                "FAIL if the response refuses or deflects on the grounds that it "
                "should load skills by name / shouldn't browse the filesystem, "
                "instead of just listing the directories."
            )
        },
    },

    # ── 4. Guard: legit skills/<name>/<driver>.py must not trip the forbid ──
    {
        "id": "S7",
        "name": "discovery-forbid-no-false-positive",
        "intent": (
            "Grader-integrity guard: a healthy run that loads a skill AND runs "
            "its driver under skills/ must still PASS — proving the forbid list "
            "(SKILL.md / context/skills) can't over-match legitimate skills/ use."
        ),
        "expected": (
            "load_skill: yfinance THEN a skills/yfinance/yfin.py driver call; "
            "neither carries SKILL.md nor context/skills, so the probe is clean."
        ),
        "tags": ["composition", "skill-discovery", "guard"],
        "prompt": "What is Apple (AAPL) trading at right now? Just the price.",
        "max_loops": 6,
        "grader": "action_used",
        "spec": {
            # require the driver too: this is the case that would expose a
            # forbid pattern accidentally matching `skills/` driver paths.
            "require": ["load_skill: yfinance", "yfin.py"],
            "forbid": ["SKILL.md", "context/skills"],
        },
    },

    # ── 4. The OTHER half of #457: save a NEW skill to context/skills/ ───────
    {
        "id": "S8",
        "name": "discovery-save-own-skill-to-context",
        "intent": (
            "The second half of #457's behavior.md fix, untested by S1-S7: when "
            "the user asks the agent to AUTHOR a new reusable skill, it must save "
            "it under context/skills/<name>/SKILL.md (where own skills live), NOT "
            "into the framework catalog skills/ (where it would be confused for a "
            "maintainer skill). This is also the context/skills counterpart to "
            "S7's guard: a legitimate authoring write touches BOTH strings the "
            "discovery probes forbid (`context/skills` + `SKILL.md`), so a PASS "
            "here proves that forbid list is scoped to *discovery*, not a blanket "
            "ban — and that an authoring conflict-check `ls context/skills/` is "
            "correct behavior, not a hunt."
        ),
        "expected": (
            "Writes the new skill to context/skills/<name>/SKILL.md; does NOT "
            "create it under the framework catalog skills/ (no `write: skills/` "
            "or `edit: skills/`)."
        ),
        "tags": ["composition", "skill-discovery", "guard", "authoring"],
        "prompt": (
            "I keep asking you to turn rough meeting notes into a tidy bulleted "
            "summary. Save that as a reusable skill of your own so you can load "
            "it next time. Keep it minimal — no web research needed, just "
            "scaffold the SKILL.md. You don't need to run it afterward."
        ),
        "max_loops": 10,  # authoring is multi-step; minimal scaffold keeps it bounded
        "grader": "action_used",
        "spec": {
            # The save lands in context/skills/<name>/SKILL.md — both substrings
            # the discovery probes forbid, legitimately present here. Forbid only
            # a write/edit INTO the framework catalog (`write: skills/...`), which
            # `write: context/skills/...` does NOT contain (the "context/" prefix
            # separates them — same distinguishing logic as S7's `yfin.py` guard).
            "require": ["context/skills", "SKILL.md"],
            "forbid": ["write: skills/", "edit: skills/"],
        },
    },

    # ── K-family: framework kernel — one tripwire per core capability ───────
    # Deliberately easy; their job is to go red instantly when a model/quant
    # swap breaks a basic path. See the module docstring for the fixture and
    # cleanup mechanics.
    {
        "id": "K1",
        "name": "kernel-schedule-persists",
        "intent": (
            "The scheduling write path: a natural-language recurring request "
            "must land as a real row in schedules.db with the right cron — "
            "verified in the STORE, not the reply (a readback can't fake it)."
        ),
        "expected": (
            "Calls the schedule tool (create) and the store afterwards holds id "
            "eval-k1-smoke with cron '0 7 * * *'; the row is deleted by cleanup."
        ),
        "tags": ["regression", "kernel", "scheduling"],
        "prompt": (
            "Set up a schedule for me: every day at 7:00 AM, run the prompt "
            "'Say good morning.' Use exactly the id 'eval-k1-smoke' for it, "
            "then confirm it's in place."
        ),
        "max_loops": 6,
        "grader": "composite",
        "spec": {
            "all": [
                {"label": "schedule-tool-called", "grader": "action_used",
                 "spec": {"require": ["schedule: create"]}},
                {"label": "row-persisted-with-cron", "grader": "anchor_equals",
                 "spec": {"equals": "0 7 * * *",
                          "anchor": {"cmd": ["python", "eval/default/_schedules.py",
                                             "show", "eval-k1-smoke"],
                                     "json_path": "cron"}}},
            ]
        },
        "cleanup": [["python", "eval/default/_schedules.py", "delete", "eval-k1-smoke"]],
    },
    {
        "id": "K2",
        "name": "kernel-memory-recall",
        "intent": (
            "The memory read path: a personal fact that exists ONLY in seeded "
            "context/memory/ must be retrieved, not guessed or refused."
        ),
        "expected": (
            "Reads the fixture memory and answers 'Dr. Amara Okafor' (profile.md); "
            "requires --fixture baseline."
        ),
        "tags": ["regression", "kernel", "memory", "fixture"],
        "prompt": "What's the name of my dentist?",
        "max_loops": 8,
        "grader": "regex_present",
        "spec": {"require": [r"Okafor"]},
    },
    {
        "id": "K3",
        "name": "kernel-delegate-handoff",
        "intent": (
            "The delegation seam: a value retrieved by a sub-agent must survive "
            "the handoff back into the parent's final reply (classic "
            "handoff-drop). The passphrase exists only in seeded memory."
        ),
        "expected": (
            "Uses the delegate tool AND the final reply carries the sub-agent's "
            "finding 'quartz-heron-42' verbatim; requires --fixture baseline."
        ),
        "tags": ["regression", "kernel", "delegation", "handoff-drop", "fixture"],
        "prompt": (
            "Use your delegate tool for this: have a sub-agent read "
            "context/memory/vault.md and report back the delegation passphrase "
            "it contains. Then tell me the passphrase."
        ),
        "max_loops": 8,
        "grader": "composite",
        "spec": {
            "all": [
                {"label": "actually-delegated", "grader": "action_used",
                 "spec": {"require": ["delegate:"]}},
                {"label": "value-survived-handoff", "grader": "regex_present",
                 "spec": {"require": [r"quartz-heron-42"]}},
            ]
        },
    },
    {
        "id": "K4",
        "name": "kernel-attach-file",
        "intent": (
            "The attachment path: 'make me a file' must end in a real attach "
            "action on the reply, not just prose describing a file."
        ),
        "expected": "Writes a small file and attaches it (an `attach:` action).",
        "tags": ["regression", "kernel", "attachments"],
        "prompt": (
            "Write a two-line note that says 'model smoke test' into a text "
            "file and attach the file to your reply."
        ),
        "max_loops": 6,
        "grader": "action_used",
        "spec": {"require": ["attach:"]},
        "budget": {"max_actions": 5},
    },
    {
        "id": "K5",
        "name": "kernel-slash-dispatch",
        "intent": (
            "The slash-command layer: a /<skill> message must be rewritten into "
            "a skill-forcing prompt the model then honors with load_skill."
        ),
        "expected": "The /humanizer message ends in `load_skill: humanizer`.",
        "tags": ["regression", "kernel", "slash-dispatch"],
        "prompt": (
            "/humanizer It is important to note that this cutting-edge solution "
            "leverages synergies to deliver best-in-class value."
        ),
        "max_loops": 5,
        "grader": "action_used",
        "spec": {"require": ["load_skill: humanizer"]},
    },
    {
        "id": "K6",
        "name": "kernel-context-retention",
        "intent": (
            "Multi-turn context integrity — the cheapest quant-degradation "
            "canary: a value stated two turns ago must be recalled exactly, "
            "with no tools involved."
        ),
        "expected": "Recalls bravo=42 from the prior turn (no tool calls needed).",
        "tags": ["regression", "kernel", "context-retention"],
        "prompts": [
            "I'm going to give you three reference codes for later: alpha=17, "
            "bravo=42, charlie=93. Just acknowledge them for now.",
            "What was the bravo code I gave you earlier? Answer with just the number.",
        ],
        "max_loops": 4,
        "grader": "regex_present",
        "spec": {"require": [r"(?<!\d)42(?!\d)"]},
        "budget": {"max_actions": 2},
    },
]

# ── RS-family: routing sweep — one trigger prompt per uncovered visible skill ─
# Graded on routing ONLY (see module docstring): did the manifest description
# carry this natural phrasing to `load_skill: <name>`? `forbid` entries mark
# documented confusable pairs. `allow_error: True` + a small max_loops keeps the
# sweep cheap: a run interrupted mid-execution still grades its routing.
_ROUTING_SWEEP: list[dict] = [
    {"skill": "deep-research-guided",
     "prompt": ("Research how small manufacturers are adopting collaborative "
                "robots and put together a short report — walk me through your "
                "plan first.")},
    {"skill": "fact-checker",
     "prompt": ("Fact-check these two claims for me: (1) the Eiffel Tower is "
                "about 330 meters tall; (2) the Great Wall of China is visible "
                "from the Moon with the naked eye.")},
    {"skill": "digest",
     "prompt": "Give me today's digest of AI news — a short briefing, fresh stories only."},
    {"skill": "gemini-image",
     "prompt": ("Generate an image of a lighthouse on a rocky coast at dawn, "
                "painterly style.")},
    {"skill": "github",
     "prompt": "Check GitHub and list the open issues on this project's repo.",
     "forbid": ["load_skill: git-contribute"]},
    {"skill": "linkedin-research",
     "prompt": ("Pull the LinkedIn backgrounds of Figma's founders — current "
                "roles and prior companies.")},
    {"skill": "medical-research",
     "prompt": ("What are the common side effects of metformin, and is there "
                "anything serious to watch for?")},
    {"skill": "playwright",
     "prompt": ("Grab the text content of https://quotes.toscrape.com/js/ — "
                "it's JavaScript-rendered, so you'll need a real browser, not a "
                "plain fetch.")},
    {"skill": "youtube-transcript",
     "prompt": ("What does this YouTube video actually say? Summarize the "
                "discussion: https://www.youtube.com/watch?v=jNQXAC9IVRw"),
     "forbid": ["load_skill: gemini-search"]},
    {"skill": "introspect",
     "prompt": "Check your own logs — any errors or weird behavior in the last day?"},
    {"skill": "identity",
     "prompt": "Show me your current identity configuration — who are you set up to be?"},
    {"skill": "skill-factory",
     "prompt": ("Create a new skill of your own for turning CSV data into "
                "markdown tables.")},
    {"skill": "xai-search",
     "prompt": ("What are people on X/Twitter saying right now about the "
                "latest Fed rate decision?")},
]


def _sweep_task(i: int, row: dict) -> dict:
    spec: dict = {"require": [f"load_skill: {row['skill']}"]}
    if row.get("forbid"):
        spec["forbid"] = row["forbid"]
    return {
        "id": f"RS{i}",
        "name": f"route-{row['skill']}",
        "intent": (
            f"Routing sweep: natural phrasing for the {row['skill']} capability "
            "must route to it via the manifest — the regression a model/quant "
            "swap or a description edit most commonly causes."
        ),
        "expected": f"Loads {row['skill']} by name; truncated execution is fine.",
        "tags": ["regression", "routing-sweep", row["skill"]],
        "prompt": row["prompt"],
        "max_loops": 5,
        "allow_error": True,  # routing is graded even if the run is interrupted
        "grader": "action_used",
        "spec": spec,
        "budget": {"max_actions": 4},  # routed-but-explored surfaces as PASS-SLOW
    }


TASKS += [_sweep_task(i, row) for i, row in enumerate(_ROUTING_SWEEP, 1)]
