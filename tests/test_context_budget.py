import json
import pytest

from src.agent.context_budget import _compute_history_budget


def test_compute_history_budget_basic():
    budget = _compute_history_budget(
        n_ctx_tokens=8192,
        static_prompt="x" * 500,
        tool_schemas=[{"function": {"name": "t", "description": "", "parameters": {}}}],
        max_tokens=2000,
    )
    expected = 8192 * 3 - 500 - len(json.dumps(
        [{"function": {"name": "t", "description": "", "parameters": {}}}]
    )) - 2000 * 3 - 500
    assert budget == expected


def test_compute_history_budget_negative_when_window_too_small():
    budget = _compute_history_budget(
        n_ctx_tokens=1024,
        static_prompt="x" * 2000,
        tool_schemas=[],
        max_tokens=16_000,
    )
    assert budget <= 0
