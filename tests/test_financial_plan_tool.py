"""Tool tests: {action, args} dispatch returns valid JSON; bad inputs return
{error,hint}; the schema is registered opt-in (unlocked by the skill)."""
import json

from src.config import AgentConfig
from src.tools.financial_plan_tool import exec_financial_plan


_INPUTS = {
    "current_age": 50, "retirement_age": 65, "end_age": 90,
    "current_balance": 500_000, "annual_contribution": 20_000,
    "annual_spending": 60_000,
}


def test_project_action_returns_rows():
    out = json.loads(exec_financial_plan(
        {"action": "project", "args": {"inputs": _INPUTS}}, AgentConfig()))
    assert out["rows"][0]["age"] == 50
    assert "ending_balance" in out


def test_montecarlo_action_is_seeded():
    a = json.loads(exec_financial_plan(
        {"action": "montecarlo", "args": {"inputs": _INPUTS, "seed": 5, "n_sims": 200}},
        AgentConfig()))
    b = json.loads(exec_financial_plan(
        {"action": "montecarlo", "args": {"inputs": _INPUTS, "seed": 5, "n_sims": 200}},
        AgentConfig()))
    assert a["success_prob"] == b["success_prob"]
    assert "threshold_met" in a


def test_stress_action_lists_scenarios():
    out = json.loads(exec_financial_plan(
        {"action": "stress", "args": {"inputs": _INPUTS, "seed": 1, "n_sims": 100}},
        AgentConfig()))
    labels = {s["scenario"] for s in out["scenarios"]}
    assert "spending_plus_20pct" in labels


def test_report_action_returns_markdown():
    out = json.loads(exec_financial_plan(
        {"action": "report", "args": {"inputs": _INPUTS, "seed": 1, "n_sims": 100}},
        AgentConfig()))
    assert "Probability of success" in out["markdown"]


def test_unknown_action_errors():
    out = json.loads(exec_financial_plan({"action": "frobnicate"}, AgentConfig()))
    assert "error" in out and "hint" in out


def test_bad_inputs_error_as_json():
    out = json.loads(exec_financial_plan(
        {"action": "project", "args": {"inputs": {"current_age": 40}}}, AgentConfig()))
    assert "error" in out


def test_schema_registered_opt_in():
    from src.tools.schemas import ALL_TOOL_SCHEMAS, get_tool_schemas
    assert "financial_plan" in ALL_TOOL_SCHEMAS
    # opt-in: excluded from the default set, present when explicitly requested.
    default_names = {s["function"]["name"] for s in get_tool_schemas()}
    assert "financial_plan" not in default_names
    requested = {s["function"]["name"] for s in get_tool_schemas(["financial_plan"])}
    assert "financial_plan" in requested
