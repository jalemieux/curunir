"""Graded eval runner for the finance persona — thin wrapper over eval.harness.

The runner engine, graders, and report generator now live in `eval/harness/`
(promoted out of this directory so other personas reuse them). This module just
builds the finance `SuiteConfig` and hands it to `eval.harness.runner.main`.

Prereqs:
    CURUNIR_PERSONA=finance python run.py        # in one shell (the SUT)
    python eval/finance/run_finance_evals.py     # in another

Options (see eval.harness.runner.build_parser):
    --host/--port      WS endpoint (default localhost:8765)
    --tag REGEX        run only tasks whose tags match (e.g. regression)
    --id  R1,F3        run only these task ids
    --fixture baseline seed fixtures/memory/baseline/ (the P/T/W tracking family)
    --no-grade         capture only, skip grading (like the legacy harness)

The judge grader (`llm_judge`) needs JUDGE_MODEL or MODEL + a key in the env
where THIS script runs (we load .env below, same as run.py).
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# Load .env so the llm_judge grader has an API key, same as run.py. The judge
# defaults to a Claude model (separate from the SUT — see eval.harness.graders);
# set JUDGE_MODEL to override.
try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from eval.harness import graders as G  # noqa: E402  re-exported for test_runner_sync
from eval.harness import runner  # noqa: E402
# Re-export the runner internals the sync tests reach for (R.run_one, R.G, …).
from eval.harness.runner import _drain_until_final, _task_timeout, run_one  # noqa: E402,F401
from eval.finance.finance_tasks import TASKS  # noqa: E402

SUITE = runner.SuiteConfig(
    name="finance",
    title="Finance Persona Evals",
    tasks=TASKS,
    results_dir=Path(__file__).parent / "results",
    fixture_memory_dir=Path(__file__).parent / "fixtures" / "memory",
)


def main() -> None:
    runner.main(SUITE)


if __name__ == "__main__":
    main()
