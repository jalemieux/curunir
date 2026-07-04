"""Graded eval runner for the reddit-research method — thin wrapper over eval.harness.

Tests the `reddit-research` skill: does extraction ADHERE to the documented
curl + JSON-API method rather than the documented mistakes (a browser /
web_fetch on a `.json` URL)? (The routing tripwires RR1/RR5 live in
eval/default — see the taxonomy in eval/README.md.) See
reddit_research_tasks.py and README.md.

Prereqs:
    CURUNIR_PERSONA=marketing python run.py                  # in one shell (the SUT)
    python eval/skills/reddit_research/run_reddit_research_evals.py # in another

`marketing` and `finance` both allowlist `reddit-research`; any persona whose
catalog includes it works. Options (see eval.harness.runner.build_parser):
    --host/--port      WS endpoint (default localhost:8765)
    --tag REGEX        run only tasks whose tags match (e.g. method, failure-mode)
    --id  RR3,RR4      run only these task ids
    --list             print id/grader/tags/name and exit (no server needed)
    --no-grade         capture only, skip grading
    -v / --verbose     stream each task's tool calls + text live

The prompts need `BRAVE_API_KEY` + network in the SUT's env for the agent to
fetch real Reddit content. The `llm_judge` checks (RR2, RR4) need JUDGE_MODEL or
MODEL + a key in the env where THIS script runs (we load .env below, same as
run.py).
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

# Load .env so the llm_judge grader has an API key, same as run.py.
try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from eval.harness import runner  # noqa: E402
from eval.skills.reddit_research.reddit_research_tasks import TASKS  # noqa: E402

SUITE = runner.SuiteConfig(
    name="reddit-research",
    title="Reddit-Research Skill Evals",
    tasks=TASKS,
    results_dir=Path(__file__).parent / "results",
)


def main() -> None:
    runner.main(SUITE)


if __name__ == "__main__":
    main()
