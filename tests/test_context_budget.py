import json
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.context_budget import _compute_history_budget, _fetch_n_ctx


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


@pytest.mark.asyncio
async def test_fetch_n_ctx_returns_from_slots():
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: [{"id": 0, "n_ctx": 8192}]

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)

    with patch("src.agent.context_budget.httpx.AsyncClient", return_value=mock_client):
        n_ctx = await _fetch_n_ctx("http://localhost:8080/v1")
    assert n_ctx == 8192


@pytest.mark.asyncio
async def test_fetch_n_ctx_raises_on_empty_slots():
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: []

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)

    with patch("src.agent.context_budget.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError, match="missing n_ctx"):
            await _fetch_n_ctx("http://localhost:8080/v1")


@pytest.mark.asyncio
async def test_fetch_n_ctx_raises_on_missing_field():
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: [{"id": 0}]

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)

    with patch("src.agent.context_budget.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError, match="missing n_ctx"):
            await _fetch_n_ctx("http://localhost:8080/v1")
