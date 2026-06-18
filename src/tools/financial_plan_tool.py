"""Opt-in `financial_plan` tool — structured-args surface over
src.financial_plan.engine.

Unlocked by the `financial-plan` skill (frontmatter `tools: financial_plan`).
One tool with an `action` + `args`; the engine is stateless (no DB), so `args`
carries the plan `inputs` plus optional `seed`/`n_sims`. Returns a JSON string
(the dispatcher contract), errors as {error,hint} — same shape as
portfolio_tool.py."""
from __future__ import annotations

import json

from src.config import AgentConfig
from src.financial_plan import engine

_ACTIONS = {
    "project": lambda a: engine.project(a["inputs"]),
    "montecarlo": lambda a: engine.monte_carlo(
        a["inputs"], n_sims=int(a.get("n_sims", 1000)), seed=int(a.get("seed", 0))),
    "stress": lambda a: engine.stress_grid(
        a["inputs"], n_sims=int(a.get("n_sims", 1000)), seed=int(a.get("seed", 0))),
    "report": lambda a: {"markdown": engine.render_markdown(
        a["inputs"], n_sims=int(a.get("n_sims", 1000)), seed=int(a.get("seed", 0)))},
}


def exec_financial_plan(args: dict, config: AgentConfig) -> str:
    action = args.get("action")
    payload = args.get("args") or {}
    handler = _ACTIONS.get(action)
    if handler is None:
        return json.dumps({"error": f"unknown action {action!r}",
                           "hint": f"valid: {sorted(_ACTIONS)}"})
    try:
        return json.dumps(handler(payload), default=str)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": str(e),
                           "hint": "pass inputs={current_age,retirement_age,end_age,"
                                   "current_balance,annual_spending,...}; see SKILL.md"})
