"""Graded eval runner for skill routing & adherence — thin wrapper over eval.harness.

Tests the three reframed live-data skills (`yfinance`, `fred`, `polymarket`) on
two axes: does the agent ROUTE to the skill for a use case that needs it, and
does it ADHERE to the skill's rules given the intent. See skill_routing_tasks.py
and README.md.

Prereqs:
    CURUNIR_PERSONA=finance python run.py             # in one shell (the SUT)
    python eval/skill_routing/run_skill_routing_evals.py  # in another

The `finance` persona allowlists all three skills; any persona whose catalog
includes them works. Options (see eval.harness.runner.build_parser):
    --host/--port      WS endpoint (default localhost:8765)
    --tag REGEX        run only tasks whose tags match (e.g. yfinance, routing)
    --id  YF1,PM2      run only these task ids
    --list             print id/grader/tags/name and exit (no server needed)
    --no-grade         capture only, skip grading
    -v / --verbose     stream each task's tool calls + text live

The `llm_judge` grader (FR3, PM3) needs JUDGE_MODEL or MODEL + a key in the env
where THIS script runs (we load .env below, same as run.py). The anchored YF4
check and the citation probes need the skills' network/keys present here too.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# Load .env so the llm_judge grader has an API key, same as run.py.
try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from eval.harness import runner  # noqa: E402
from eval.skill_routing.skill_routing_tasks import TASKS  # noqa: E402

SUITE = runner.SuiteConfig(
    name="skill-routing",
    title="Skill Routing & Adherence Evals",
    tasks=TASKS,
    results_dir=Path(__file__).parent / "results",
)


def main() -> None:
    runner.main(SUITE)


if __name__ == "__main__":
    main()
