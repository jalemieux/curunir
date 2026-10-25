"""Contract tests for the R1/R2/R4 market-data graders (issue #340).

These tasks fetch a single market datum. The grader's contract is an OUTCOME
("the real driver ran and nothing improvised around it"), not a MECHANISM
("`load_skill` was called"). For `yfinance`/`sec-edgar`, `load_skill` unlocks
no opt-in tool, so requiring it asserts an internal step rather than the result;
the driver-call check plus a `forbid` list (web_fetch/curl/import) and the
action budget already pin the outcome.

The three strategies a model picks for "fetch a datum" (from #340):
  (a) load_skill -> driver         — clean
  (b) glob/read/find thrash -> driver — correct but flails (budget catches it)
  (c) grep -> web_fetch Yahoo      — the genuinely bad, non-citable path

These tests are hermetic: they synthesize `Result` objects with the normalized
action strings graders see (see runner._normalize_action) and assert against
the REAL task specs in finance_tasks.TASKS, so the config can't drift from the
contract. No model calls, no network, no live anchors.
"""

from __future__ import annotations

import eval.harness.graders as G
from eval.finance.finance_tasks import TASKS


def _task(task_id: str) -> dict:
    return next(t for t in TASKS if t["id"] == task_id)


def _sub_spec(task: dict, label: str) -> dict:
    """The `spec` of a composite sub-grader, found by its label."""
    return next(s["spec"] for s in task["spec"]["all"] if s["label"] == label)


def _result(actions: list[str]) -> G.Result:
    return G.Result(final_text="The answer is 123.", actions=actions)


# Normalized action strings, exactly as runner._normalize_action emits them.
_LOAD_YF = "load_skill: yfinance"
_DRIVE_YF = "bash: python skills/yfinance/yfin.py quote AAPL"
_DRIVE_YF_PE = "bash: python skills/yfinance/yfin.py multiples NVDA"
_WEB_FETCH = "web_fetch: https://finance.yahoo.com/quote/AAPL"
_GREP = "grep: yfinance skills/"
_GLOB = "glob: skills/**/*.py"
_READ = "read: skills/yfinance/SKILL.md"
_LOAD_EDGAR = "load_skill: sec-edgar"
_DRIVE_EDGAR = "bash: python skills/sec-edgar/edgar.py lookup LLY"
_CURL_SEC = "bash: curl https://www.sec.gov/cgi-bin/browse-edgar?company=LLY"


# ── config shape: load_skill dropped, forbid added ──────────────────────────
def test_r1_spec_drops_load_skill_and_forbids_improvising():
    spec = _task("R1")["spec"]
    assert spec["require"] == ["yfin.py"]
    assert "load_skill: yfinance" not in spec["require"]
    for sub in ("web_fetch", "curl", "import yfinance"):
        assert sub in spec["forbid"]


def test_r2_used_yfin_spec_drops_load_skill_and_forbids_improvising():
    spec = _sub_spec(_task("R2"), "used-yfin")
    assert spec["require"] == ["yfin.py"]
    assert "load_skill: yfinance" not in spec["require"]
    for sub in ("web_fetch", "curl", "import yfinance"):
        assert sub in spec["forbid"]


def test_r4_used_edgar_spec_drops_load_skill_and_forbids_scrape():
    spec = _sub_spec(_task("R4"), "used-edgar")
    assert spec["require"] == ["edgar.py"]
    assert "load_skill: sec-edgar" not in spec["require"]
    for sub in ("web_fetch", "curl"):
        assert sub in spec["forbid"]


# ── R1: the three observed strategies grade correctly ───────────────────────
def test_r1_clean_path_passes():
    """load_skill -> driver, ~2 actions: a clean PASS."""
    status, _why, _ = G.grade_detailed(_task("R1"), _result([_LOAD_YF, _DRIVE_YF]))
    assert status == G.PASS


def test_r1_thrash_path_is_pass_slow_not_fail():
    """glob/read thrash that still runs the driver: the driver ran, so the
    routing contract is met — the 8-action budget overrun is what flags it,
    demoting to PASS_SLOW. Under the old `load_skill`-required spec this was a
    hard FAIL for the wrong reason (a missing mechanism)."""
    thrash = [_GLOB, _READ, _GREP, _GLOB, _READ, _GREP, _READ, _DRIVE_YF]
    assert len(thrash) > _task("R1")["budget"]["max_actions"]
    status, _why, _ = G.grade_detailed(_task("R1"), _result(thrash))
    assert status == G.PASS_SLOW


def test_r1_web_scrape_path_fails():
    """grep -> web_fetch Yahoo, no driver: the genuinely bad path. Fails because
    the driver never ran (require yfin.py) and a forbidden scrape did."""
    status, _why, _ = G.grade_detailed(_task("R1"), _result([_GREP, _WEB_FETCH]))
    assert status == G.FAIL


def test_r1_driver_plus_web_fetch_trips_forbid():
    """Even WITH the driver, a web_fetch around it is forbidden — the forbid list
    is what catches an improvised scrape layered on top of a real call."""
    status, why, _ = G.grade_detailed(
        _task("R1"), _result([_LOAD_YF, _DRIVE_YF, _WEB_FETCH])
    )
    assert status == G.FAIL
    assert "web_fetch" in why


# ── R2 used-yfin sub-grader ─────────────────────────────────────────────────
def test_r2_used_yfin_clean_path_passes():
    spec = _sub_spec(_task("R2"), "used-yfin")
    status, _why = G.action_used(_result([_LOAD_YF, _DRIVE_YF_PE]), spec)
    assert status == G.PASS


def test_r2_used_yfin_web_scrape_fails():
    spec = _sub_spec(_task("R2"), "used-yfin")
    status, _why = G.action_used(_result([_GREP, _WEB_FETCH]), spec)
    assert status == G.FAIL


# ── R4 used-edgar sub-grader ────────────────────────────────────────────────
def test_r4_used_edgar_clean_path_passes():
    spec = _sub_spec(_task("R4"), "used-edgar")
    status, _why = G.action_used(_result([_LOAD_EDGAR, _DRIVE_EDGAR]), spec)
    assert status == G.PASS


def test_r4_used_edgar_curl_scrape_fails():
    spec = _sub_spec(_task("R4"), "used-edgar")
    status, why = G.action_used(_result([_DRIVE_EDGAR, _CURL_SEC]), spec)
    assert status == G.FAIL
    assert "curl" in why
