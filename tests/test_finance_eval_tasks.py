# tests/test_finance_eval_tasks.py
"""Structural validation of the finance eval suite's task table.

The tasks are data (`eval/finance/finance_tasks.py`); a typo'd grader key or
malformed composite spec would otherwise only surface mid-run against a live
instance. These checks keep the table loadable and dispatchable offline.
"""
from eval.finance.finance_tasks import TASKS
from eval.harness.graders import GRADERS


def _assert_grader_resolves(grader: str, spec: dict) -> None:
    assert grader in GRADERS, f"unknown grader {grader!r}"
    if grader == "composite":
        for sub in spec["all"]:
            _assert_grader_resolves(sub["grader"], sub.get("spec", {}))


def test_every_task_grader_resolves():
    for task in TASKS:
        _assert_grader_resolves(task["grader"], task.get("spec", {}))


def test_task_ids_unique():
    ids = [t["id"] for t in TASKS]
    assert len(ids) == len(set(ids))


def test_w4_idea_log_capture_task():
    """W4 (#506): 'log this idea' → lightweight idea-log capture, not a memo.

    Multi-turn tracking task: the write turn must touch idea-log.md and the
    readback turn is judged for status / promote-kill / Last touched, with the
    heavy orchestrators forbidden and a max_actions budget so a correct-but-
    loopy run surfaces as PASS-SLOW.
    """
    (w4,) = [t for t in TASKS if t["id"] == "W4"]
    assert "tracking" in w4["tags"]  # runs against the seeded fixture
    assert len(w4["prompts"]) == 2  # write turn + readback turn
    assert "budget" in w4 and "max_actions" in w4["budget"]

    spec_text = repr(w4["spec"])
    assert "idea-log.md" in spec_text
    assert "load_skill: investment-memo" in spec_text  # forbidden escalation
