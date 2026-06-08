"""Graded eval runner for the default persona — thin wrapper over eval.harness.

Mirrors `eval/finance/run_finance_evals.py`: builds a default `SuiteConfig` and
hands it to the shared `eval.harness.runner.main`. The task list
(`default_tasks.TASKS`) is currently empty — see default_tasks.py.

Prereqs:
    CURUNIR_PERSONA=default python run.py         # in one shell (the SUT)
    python eval/default/run_default_evals.py      # in another

Options: see eval.harness.runner.build_parser (--host/--port/--tag/--id/
--no-grade/--fixture/--verbose). The judge grader needs JUDGE_MODEL or MODEL +
a key in this script's env (we load .env below).
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from eval.harness import runner  # noqa: E402
from eval.default.default_tasks import TASKS  # noqa: E402

SUITE = runner.SuiteConfig(
    name="default",
    title="Default Persona Evals",
    tasks=TASKS,
    results_dir=Path(__file__).parent / "results",
    fixture_memory_dir=Path(__file__).parent / "fixtures" / "memory",
)


def main() -> None:
    runner.main(SUITE)


if __name__ == "__main__":
    main()
