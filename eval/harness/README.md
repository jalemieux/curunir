# Graded Eval Harness

The shared, persona-agnostic engine every graded suite runs on. Full
documentation — flags, report format, statuses, the grader catalog, anchoring,
and the `Result` contract — lives in [`eval/README.md`](../README.md); this
file is just the map.

| File | Role |
|------|------|
| `graders.py` | Pure graders `(result, spec) -> (status, why)`; the `GRADERS` dispatch + `grade()` / `grade_detailed()` |
| `runner.py` | `SuiteConfig` + the generic engine: drives the WS channel, builds a `Result`, grades, writes JSON/MD/HTML reports |
| `test_runner_sync.py` | Zero-token regression tests for the runner's WS frame handling (`python eval/harness/test_runner_sync.py` — no SUT, no keys) |

A suite is a **thin shim** (~40 lines): a `<name>_tasks.py` data file plus a
runner that builds a `SuiteConfig(name, title, tasks, results_dir,
fixture_memory_dir)` and calls `runner.main`. Persona suites live at
`eval/<persona>/`; skill suites at `eval/skills/<name>/`.
