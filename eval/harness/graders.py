"""Graders for the finance-persona eval suite.

Each grader is a pure function with the signature

    (result: Result, spec: dict) -> Verdict

where `Verdict = (status, why)`, `status` is one of the `Status` constants
below, and `why` is a one-line human-readable reason. `grade(task, result)`
is the top-level entry point: it resolves any live `anchor`, wraps the inner
grader in a process budget when the task declares one, and dispatches by name.

`result` is whatever the runner captured from the system under test. The only
fields graders touch:

    result.final_text : str        # the agent's textual answer
    result.actions    : list[str]  # streamed tool-call summaries, e.g.
                                    #   "load_skill: investment-memo"
                                    #   "bash: python skills/yfinance/yfin.py quote AAPL"
                                    #   "attach: workspace/memo.pdf"
    result.wall_ms    : float
    result.turns      : int
    result.tokens_out : int
    result.error      : str | None

Graders assert OUTCOMES at the boundary, never internals. Where a routing or
privacy *contract* genuinely is "which capability ran" or "what was sent out",
we inspect `actions` — that is the contract, not an implementation detail.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable

# ── statuses ────────────────────────────────────────────────────────────────
PASS = "pass"
FAIL = "fail"
PASS_SLOW = "pass-slow"  # correct, but over a process budget — a distinct signal
ERROR = "error"  # the grader itself could not run (e.g. judge model unreachable)

Verdict = tuple[str, str]


@dataclass
class Result:
    """The system-under-test output a grader sees. The runner builds this."""

    final_text: str = ""
    actions: list[str] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)  # PDFs etc. on the final reply
    wall_ms: float = 0.0
    turns: int = 0
    tokens_out: int = 0
    stats: dict = field(default_factory=dict)  # full stats dict from the server
    error: str | None = None


# ── helpers ─────────────────────────────────────────────────────────────────
_NUM_RE = re.compile(r"(-?\$?\d[\d,]*(?:\.\d+)?)\s*([A-Za-z]{0,8})")


def _numbers(text: str) -> list[float]:
    """All numeric tokens in `text`, as floats — scale-suffix aware.

    A trailing magnitude word is folded in, so "$3.10 trillion" reads as
    3.1e12 rather than a bare 3.1 — otherwise `_closest` matching a market cap
    picks an unrelated small number (e.g. the share price) in the same answer.
    `_SCALE` is defined below in the reconciliation section.
    """
    out = []
    for m in _NUM_RE.finditer(text):
        try:
            val = float(m.group(1).replace(",", "").replace("$", ""))
        except ValueError:
            continue
        suf = (m.group(2) or "").lower().strip(".")
        if suf in _SCALE:
            val *= _SCALE[suf]
        out.append(val)
    return out


def _closest(nums: list[float], target: float) -> float | None:
    """The number whose magnitude is closest to `target` (ratio distance).

    Ratio distance, not absolute, so "P/E 32.4" wins over an unrelated
    "$5,114,022,068,224" market cap that happens to appear in the same answer.
    """
    if not nums:
        return None
    return min(nums, key=lambda n: abs((n - target) / target) if target else abs(n - target))


def _actions_text(result: Result) -> str:
    return "\n".join(result.actions)


def resolve_anchor(spec: dict) -> dict:
    """Recompute `spec['expected']` from live source data if an anchor is set.

    An anchor runs the SAME CLI the agent uses, so the eval stays valid across
    data reseeds and never silently drifts from the agent. Shape:

        "anchor": {"cmd": ["python", "skills/yfinance/yfin.py", "multiples", "NVDA"],
                   "json_path": "trailing_pe"}

    `json_path` is a dotted path into the parsed JSON. On any failure the anchor
    is skipped and a pre-baked `spec['expected']` (if present) is used instead.
    """
    anchor = spec.get("anchor")
    if not anchor:
        return spec
    try:
        proc = subprocess.run(
            anchor["cmd"], capture_output=True, text=True, timeout=60, cwd=_repo_root()
        )
        data = json.loads(proc.stdout)
        for key in anchor.get("json_path", "").split("."):
            if key:
                data = data[key]
        spec = {**spec, "expected": data, "_anchored": True}
    except Exception as exc:  # noqa: BLE001 — anchor is best-effort
        spec = {**spec, "_anchor_error": str(exc)}
    return spec


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ── graders ─────────────────────────────────────────────────────────────────
def exact_match(result: Result, spec: dict) -> Verdict:
    """Pull a token via `extract_regex` (group 1) and compare to `expected`."""
    m = re.search(spec["extract_regex"], result.final_text)
    if not m:
        return FAIL, f"no token matched /{spec['extract_regex']}/"
    got = m.group(1) if m.groups() else m.group(0)
    want = str(spec["expected"])
    norm = spec.get("normalize")
    if norm == "lstrip0":  # CIKs etc. — compare ignoring leading zeros
        got_c, want_c = got.lstrip("0"), want.lstrip("0")
    else:
        got_c, want_c = got.strip(), want.strip()
    if got_c == want_c:
        return PASS, f"{got} == {want}"
    return FAIL, f"got {got!r}, expected {want!r}"


def numeric_tolerance(result: Result, spec: dict) -> Verdict:
    """The answer must contain a number within `tolerance_pct` of `expected`.

    Picks the number in the answer closest in magnitude to the target, so an
    unrelated figure elsewhere in the prose doesn't sink the grade.
    """
    if "expected" not in spec:
        return ERROR, f"no expected value (anchor failed: {spec.get('_anchor_error')})"
    target = float(spec["expected"])
    tol = spec.get("tolerance_pct", 5) / 100.0
    cand = _closest(_numbers(result.final_text), target)
    if cand is None:
        return FAIL, "no number found in answer"
    rel = abs(cand - target) / target if target else abs(cand - target)
    src = " (anchored live)" if spec.get("_anchored") else ""
    if rel <= tol:
        return PASS, f"{cand:g} within {tol:.0%} of {target:g}{src}"
    return FAIL, f"{cand:g} is {rel:.0%} off {target:g}{src} (tol {tol:.0%})"


def set_match(result: Result, spec: dict) -> Verdict:
    """Every item in `expected` must appear (case-insensitive substring).

    `groups` (list of lists) requires at least one member of each group — use
    for "name a winner AND a loser" where each side has synonyms.
    """
    hay = result.final_text.lower()
    missing = [x for x in spec.get("expected", []) if x.lower() not in hay]
    for i, group in enumerate(spec.get("groups", [])):
        if not any(g.lower() in hay for g in group):
            missing.append(f"<group {i}: {'|'.join(group)}>")
    if missing:
        return FAIL, "missing: " + ", ".join(missing)
    return PASS, "all required items present"


def regex_present(result: Result, spec: dict) -> Verdict:
    """All `require` regexes must match; no `forbid` regex may match."""
    text = result.final_text
    for pat in spec.get("require", []):
        if not re.search(pat, text, re.IGNORECASE):
            return FAIL, f"required pattern absent: /{pat}/"
    for pat in spec.get("forbid", []):
        if re.search(pat, text, re.IGNORECASE):
            return FAIL, f"forbidden pattern present: /{pat}/"
    return PASS, "patterns satisfied"


def action_used(result: Result, spec: dict) -> Verdict:
    """Assert tool-call routing: required actions ran, forbidden ones didn't.

    The contract here IS the action (e.g. "a recommendation routes to
    investment-memo, not deep-research"; "no private holding left the box").
      require      : every substring must appear in some action
      require_any  : at least one substring must appear
      forbid       : no substring may appear in any action
    """
    text = _actions_text(result)
    for sub in spec.get("require", []):
        if sub not in text:
            return FAIL, f"expected action containing {sub!r}"
    req_any = spec.get("require_any")
    if req_any and not any(sub in text for sub in req_any):
        return FAIL, f"expected one of {req_any}"
    for sub in spec.get("forbid", []):
        if sub in text:
            return FAIL, f"forbidden action present: {sub!r}"
    return PASS, "routing/actions as expected"


def anchor_equals(result: Result, spec: dict) -> Verdict:
    """PASS iff the anchor-resolved value equals `spec['equals']` — a pure
    STORE-side check, deliberately blind to the reply text.

    Use when the contract is "the write actually persisted": the anchor cmd
    queries the store (schedules.db, portfolio.db, …) at grade time and this
    grader compares what it finds against a frozen expectation. A readback
    answered from conversation history can't fake this — only a durable write
    satisfies it. `resolve_anchor` puts the queried value in `expected`.
    """
    if "expected" not in spec:
        return ERROR, f"anchor did not resolve ({spec.get('_anchor_error')})"
    want, got = spec.get("equals"), spec["expected"]
    if got == want:
        return PASS, f"store value {got!r} matches"
    return FAIL, f"store has {got!r}, expected {want!r}"


# A capable model, separate from the system under test, judges structural
# tasks. Deliberately NOT $MODEL: a model grading its own output is a known
# eval anti-pattern. Override with $JUDGE_MODEL (needs the matching API key).
DEFAULT_JUDGE_MODEL = "anthropic/claude-sonnet-4-6"


def llm_judge(result: Result, spec: dict) -> Verdict:
    """Grade against a rubric with explicit PASS/FAIL conditions via an LLM.

    Use only when correctness is structural (a hedge is present, disconfirming
    evidence is surfaced) and no single value captures it. The rubric must
    state PASS and FAIL conditions crisply. Judge model = $JUDGE_MODEL, else a
    capable Claude default — never the SUT model.
    """
    model = os.environ.get("JUDGE_MODEL") or DEFAULT_JUDGE_MODEL
    try:
        import litellm
    except ImportError:
        return ERROR, "litellm not importable"

    rubric = spec["rubric"]
    prompt = (
        "You are grading one response from a personal-finance assistant.\n"
        "Apply the rubric strictly. Reply with a single line and nothing else:\n"
        "  VERDICT: PASS|FAIL — <one-sentence reason>\n\n"
        f"RUBRIC:\n{rubric}\n\n"
        f"ASSISTANT RESPONSE:\n\"\"\"\n{result.final_text}\n\"\"\"\n"
    )
    try:
        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=512,  # headroom for reasoning models that spend tokens thinking
        )
        msg = resp["choices"][0]["message"]
        # Reasoning models may leave `content` empty and put text in
        # `reasoning_content`; fall back so the verdict line is still found.
        verdict = (msg.get("content") or msg.get("reasoning_content") or "").strip()
    except Exception as exc:  # noqa: BLE001
        return ERROR, f"judge call failed ({model}): {exc}"

    if not verdict:
        return ERROR, f"empty judge response from {model}"
    m = re.search(r"VERDICT:\s*(PASS|FAIL)\s*[—:-]*\s*(.*)", verdict, re.IGNORECASE | re.DOTALL)
    if not m:
        return ERROR, f"unparseable judge output: {verdict[:120]}"
    status = PASS if m.group(1).upper() == "PASS" else FAIL
    return status, m.group(2).strip().splitlines()[0][:160] or "(no reason given)"


# ── reconciliation (position-tracking suite) ────────────────────────────────
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6,
          "b": 1e9, "bn": 1e9, "billion": 1e9, "t": 1e12, "trillion": 1e12}


def _money_after(text: str, labels: list[str], window: int = 40) -> float | None:
    """First dollar figure following any of `labels`, scale-suffix aware.

    Anchors on a label (e.g. "total assets") and grabs the next dollar amount
    within `window` chars, honouring a trailing scale word so "$4.13M" and
    "4.13 million" both read as 4_130_000. Returns None if no label matched —
    which the caller treats as "no clear balance sheet presented".
    """
    for lab in labels:
        m = re.search(
            lab + r"[^0-9$]{0," + str(window) + r"}\$?\s?(-?[\d,]+(?:\.\d+)?)\s*([A-Za-z]{0,8})",
            text, re.IGNORECASE,
        )
        if not m:
            continue
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        suf = (m.group(2) or "").lower().strip(".")
        if suf in _SCALE:
            val *= _SCALE[suf]
        return val
    return None


def reconciles(result: Result, spec: dict) -> Verdict:
    """The agent's stated balance sheet must internally add up (and match truth).

    Extracts the agent's stated *total assets*, *total liabilities*, and *net
    worth* (label-anchored), then asserts `assets − liabilities == net_worth`
    within `tolerance` dollars. When an anchor resolved `expected` (the true net
    worth), also asserts the stated net worth matches it within
    `anchor_tolerance_pct`. If any of the three labels is absent it FAILs — an
    agent that won't present a clear balance sheet is itself failing the
    contract (a vague "about $4M" is not a reconciling answer).
    """
    text = result.final_text
    assets = _money_after(text, [r"total\s+assets", r"assets\s+total", r"gross\s+assets"])
    liab = _money_after(text, [r"total\s+liabilit", r"total\s+debt", r"liabilit"])
    nw = _money_after(text, [r"total\s+net[\s-]*worth", r"net[\s-]*worth",
                             r"net\s+liquid\s+(?:net\s+)?worth"])
    missing = [n for n, v in (("assets", assets), ("liabilities", liab),
                              ("net worth", nw)) if v is None]
    if missing:
        return FAIL, "no clear " + ", ".join(missing) + " figure presented (not a balance sheet)"

    tol = spec.get("tolerance", 1.0)
    diff = abs((assets - liab) - nw)
    if diff > tol:
        return FAIL, (f"does not reconcile: assets {assets:,.0f} − liabilities {liab:,.0f} "
                      f"= {assets - liab:,.0f} ≠ stated net worth {nw:,.0f} (off ${diff:,.0f})")

    if "expected" in spec:
        target = float(spec["expected"])
        atol = spec.get("anchor_tolerance_pct", 2) / 100.0
        rel = abs(nw - target) / target if target else abs(nw - target)
        src = " (anchored)" if spec.get("_anchored") else ""
        if rel > atol:
            return FAIL, f"net worth {nw:,.0f} is {rel:.1%} off truth {target:,.0f}{src} (tol {atol:.0%})"
        return PASS, f"reconciles & matches truth: {assets:,.0f} − {liab:,.0f} = {nw:,.0f}{src}"
    return PASS, f"reconciles: {assets:,.0f} − {liab:,.0f} = {nw:,.0f}"


def composite(result: Result, spec: dict) -> Verdict:
    """AND a list of sub-graders. FAIL if any fails; PASS_SLOW if any is slow."""
    slow = False
    reasons = []
    for sub in spec["all"]:
        sub_spec = resolve_anchor(sub.get("spec", {}))
        status, why = GRADERS[sub["grader"]](result, sub_spec)
        label = sub.get("label", sub["grader"])
        if status == FAIL:
            return FAIL, f"[{label}] {why}"
        if status == ERROR:
            return ERROR, f"[{label}] {why}"
        if status == PASS_SLOW:
            slow = True
        reasons.append(f"{label}✓")
    return (PASS_SLOW if slow else PASS), " ".join(reasons)


GRADERS: dict[str, Callable[[Result, dict], Verdict]] = {
    "exact_match": exact_match,
    "numeric_tolerance": numeric_tolerance,
    "set_match": set_match,
    "regex_present": regex_present,
    "action_used": action_used,
    "anchor_equals": anchor_equals,
    "llm_judge": llm_judge,
    "reconciles": reconciles,
    "composite": composite,
}


# ── top-level ───────────────────────────────────────────────────────────────
def _apply_budget(task: dict, result: Result, status: str, why: str) -> Verdict:
    """Demote a PASS to PASS_SLOW when the task declares a process budget the
    run blew. A correct-but-expensive answer is a distinct, valuable signal —
    it is the axis decomposition/perf work moves — so we never fold it into FAIL.
    """
    budget = task.get("budget")
    if not budget or status != PASS:
        return status, why
    over = []
    if "max_actions" in budget and len(result.actions) > budget["max_actions"]:
        over.append(f"{len(result.actions)} actions > {budget['max_actions']}")
    if "max_wall_ms" in budget and result.wall_ms > budget["max_wall_ms"]:
        over.append(f"{result.wall_ms:.0f}ms > {budget['max_wall_ms']}ms")
    if "max_turns" in budget and result.turns > budget["max_turns"]:
        over.append(f"{result.turns} turns > {budget['max_turns']}")
    if over:
        return PASS_SLOW, f"{why}  [over budget: {'; '.join(over)}]"
    return status, why


def grade_detailed(task: dict, result: Result) -> tuple[str, str, list[dict]]:
    """Like grade(), but also return a per-check breakdown for the report.

    Returns (status, why, checks) where `checks` is a list of
    {label, grader, status, why} — one entry per sub-grader for a composite
    (ALL evaluated, so the report shows every check, not just the first
    failure), or a single entry for a simple grader.
    """
    grader_name = task.get("grader")
    if result.error and grader_name != "regex_present" and not task.get("allow_error"):
        why = f"system error: {result.error}"
        return FAIL, why, [{"label": "system", "grader": grader_name or "?",
                            "status": FAIL, "why": why}]
    if grader_name not in GRADERS:
        return ERROR, f"unknown grader {grader_name!r}", []

    checks: list[dict] = []
    if grader_name == "composite":
        passed = []
        first_fail = first_error = None
        slow = False
        for sub in task["spec"]["all"]:
            spec = resolve_anchor(sub.get("spec", {}))
            st, why = GRADERS[sub["grader"]](result, spec)
            label = sub.get("label", sub["grader"])
            checks.append({"label": label, "grader": sub["grader"], "status": st, "why": why})
            if st == FAIL and first_fail is None:
                first_fail = f"[{label}] {why}"
            elif st == ERROR and first_error is None:
                first_error = f"[{label}] {why}"
            elif st == PASS_SLOW:
                slow = True
            if st == PASS:
                passed.append(f"{label}✓")
        if first_fail is not None:
            status, why = FAIL, first_fail
        elif first_error is not None:
            status, why = ERROR, first_error
        else:
            status, why = (PASS_SLOW if slow else PASS), " ".join(passed)
    else:
        spec = resolve_anchor(task.get("spec", {}))
        status, why = GRADERS[grader_name](result, spec)
        checks.append({"label": grader_name, "grader": grader_name,
                       "status": status, "why": why})

    status, why = _apply_budget(task, result, status, why)
    return status, why, checks


def grade(task: dict, result: Result) -> Verdict:
    """Resolve anchors, dispatch the grader, then apply any process budget."""
    status, why, _ = grade_detailed(task, result)
    return status, why
