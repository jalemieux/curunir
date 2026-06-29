"""Graded eval runner for the web-search routing reflex — thin wrapper over eval.harness.

Tests the PR #458 / #450 fix on two axes: does the agent ROUTE to the
`web-search` skill (load it and curl the Brave API) for a consumer/local-business
lookup, and does it AVOID the rediscovery loop (no `web_fetch`/raw-`curl` of the
bot-blocked sites — Google search, Yelp, Reddit)? See web_search_tasks.py and
README.md.

Prereqs:
    CURUNIR_PERSONA=default python run.py             # in one shell (the SUT)
    python eval/web_search/run_web_search_evals.py    # in another

`default`, `finance`, `marketing`, and `companion` all allowlist `web-search` and
carry the routing reflex; `default` is the most realistic routing test (full
catalog present, so the agent must still pick web-search). Options (see
eval.harness.runner.build_parser):
    --host/--port      WS endpoint (default localhost:8765)
    --tag REGEX        run only tasks whose tags match (e.g. routing, failure-mode)
    --id  WS1,WS2      run only these task ids
    --list             print id/grader/tags/name and exit (no server needed)
    --no-grade         capture only, skip grading
    -v / --verbose     stream each task's tool calls + text live

The prompts need `BRAVE_API_KEY` + network in the SUT's env for the agent to
produce a grounded answer. The `llm_judge` checks (WS3, WS4) need JUDGE_MODEL or
MODEL + a key in the env where THIS script runs (we load .env below, same as
run.py).
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
from eval.web_search.web_search_tasks import TASKS  # noqa: E402

SUITE = runner.SuiteConfig(
    name="web-search",
    title="Web-Search Routing Evals",
    tasks=TASKS,
    results_dir=Path(__file__).parent / "results",
)


def main() -> None:
    runner.main(SUITE)


if __name__ == "__main__":
    main()
