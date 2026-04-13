from unittest.mock import AsyncMock, patch

import pytest

from src.agent.context_budget import _fetch_n_ctx, resolve_llamacpp_budget
from src.config import AgentConfig


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


@pytest.mark.asyncio
async def test_resolve_budget_sets_n_ctx_and_keeps_max_tokens(tmp_context, tmp_skills):
    cfg = AgentConfig(
        api_base="http://localhost:8080/v1",
        max_tokens=2000,
        identity_file=tmp_context / "identity.md",
        context_dir=tmp_context,
        skills_dir=tmp_skills,
    )

    async def fake_fetch(api_base):
        return 8192

    with patch("src.agent.context_budget._fetch_n_ctx", fake_fetch):
        await resolve_llamacpp_budget(cfg)

    assert cfg.n_ctx == 8192
    assert cfg.max_tokens == 2000


@pytest.mark.asyncio
async def test_resolve_budget_clamps_max_tokens(tmp_context, tmp_skills):
    cfg = AgentConfig(
        api_base="http://localhost:8080/v1",
        max_tokens=20_000,
        identity_file=tmp_context / "identity.md",
        context_dir=tmp_context,
        skills_dir=tmp_skills,
    )

    async def fake_fetch(api_base):
        return 32768

    with patch("src.agent.context_budget._fetch_n_ctx", fake_fetch):
        await resolve_llamacpp_budget(cfg)

    assert cfg.n_ctx == 32768
    assert cfg.max_tokens == 16_384
