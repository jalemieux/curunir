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
]
