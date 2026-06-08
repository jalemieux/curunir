"""Default-persona eval suite — tasks as data.

Run with `CURUNIR_PERSONA=default` (or unset, which falls back to default).
This suite is graded by the shared `eval.harness` engine — see the finance
suite (`eval/finance/finance_tasks.py`) for the task-dict schema and grader
catalog.

It is intentionally EMPTY for now: the shared harness was promoted out of
`eval/finance/` so the default persona can be held to the same graded bar, but
the task authoring (converting the capture-only prompts in
`eval/simple_evals.md` / `eval/advanced_evals.md` into graded task dicts) is a
separate, judgment-heavy step. Add tasks here incrementally.

Until then `python eval/default/run_default_evals.py` prints "No tasks matched".
"""

TASKS: list[dict] = []
