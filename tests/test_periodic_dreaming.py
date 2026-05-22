"""Tests for run.periodic_dreaming — the periodic agent-loop system job
that fires the dreaming skill."""
import asyncio
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_periodic_dreaming_fires_skill_through_agent_handle(
    agent_config, tmp_skills,
):
    """One tick → agent.handle is called with the dreaming skill content as
    system_task_prompt and a session_id under the system:dreaming: namespace."""
    from run import periodic_dreaming

    skill_dir = tmp_skills / "dreaming"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: dreaming\ndescription: tidy memory\n---\n"
        "Do the dreaming work."
    )

    mock_agent = AsyncMock()
    mock_agent.config = agent_config

    called = asyncio.Event()

    async def signal(**kwargs):
        called.set()

    mock_agent.handle.side_effect = signal

    task = asyncio.create_task(periodic_dreaming(mock_agent, interval_sec=0))
    try:
        await asyncio.wait_for(called.wait(), timeout=2.0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    call_kwargs = mock_agent.handle.call_args.kwargs
    assert "Do the dreaming work." in call_kwargs["system_task_prompt"]
    assert call_kwargs["message"] == ""
    assert call_kwargs["session_id"].startswith("system:dreaming:")
