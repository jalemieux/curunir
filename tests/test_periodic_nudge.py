"""Tests for run.periodic_nudge — tier selection and the ladder/weekly logic."""
import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest


def _write_state(path, last_user_msg_at, tiers_sent=None, last_weekly_at=0.0):
    path.write_text(json.dumps({
        "last_user_msg_at": last_user_msg_at,
        "tiers_sent_this_idle": tiers_sent or [],
        "last_weekly_at": last_weekly_at,
    }))


async def _run_one_tick(agent, state_path):
    """Run periodic_nudge with interval=0 long enough for one tick to fire,
    then cancel. Returns the agent.handle call kwargs (or None if not called)."""
    from run import periodic_nudge

    task = asyncio.create_task(periodic_nudge(
        agent,
        interval_sec=0,
        state_path=state_path,
        recipient="user@example.com",
        enabled=True,
    ))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    if agent.handle.called:
        return agent.handle.call_args.kwargs
    return None


@pytest.fixture
def mock_agent(agent_config, tmp_skills):
    skill_dir = tmp_skills / "nudge"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: nudge\ndescription: nudge user\n---\nDo the nudging."
    )
    agent = AsyncMock()
    agent.config = agent_config
    return agent


@pytest.mark.asyncio
async def test_fires_2d_when_idle_3_days(mock_agent, tmp_path):
    state_path = tmp_path / "nudge_state.json"
    _write_state(state_path, last_user_msg_at=time.time() - 3 * 86400)

    kwargs = await _run_one_tick(mock_agent, state_path)
    assert kwargs is not None
    assert "Do the nudging." in kwargs["system_task_prompt"]
    assert "Tier: 2d" in kwargs["system_task_prompt"]
    assert kwargs["session_id"].startswith("system:nudge:2d:")

    state = json.loads(state_path.read_text())
    assert "2d" in state["tiers_sent_this_idle"]


@pytest.mark.asyncio
async def test_does_not_fire_2d_if_already_sent(mock_agent, tmp_path):
    state_path = tmp_path / "nudge_state.json"
    _write_state(
        state_path,
        last_user_msg_at=time.time() - 3 * 86400,
        tiers_sent=["2d"],
    )

    kwargs = await _run_one_tick(mock_agent, state_path)
    assert kwargs is None


@pytest.mark.asyncio
async def test_fires_14d_when_idle_15_days(mock_agent, tmp_path):
    state_path = tmp_path / "nudge_state.json"
    _write_state(
        state_path,
        last_user_msg_at=time.time() - 15 * 86400,
        tiers_sent=["2d", "7d"],
    )

    kwargs = await _run_one_tick(mock_agent, state_path)
    assert kwargs is not None
    assert "Tier: 14d" in kwargs["system_task_prompt"]
    assert kwargs["session_id"].startswith("system:nudge:14d:")


@pytest.mark.asyncio
async def test_weekly_fires_for_active_user_when_due(mock_agent, tmp_path):
    state_path = tmp_path / "nudge_state.json"
    _write_state(
        state_path,
        last_user_msg_at=time.time() - 3600,
        last_weekly_at=time.time() - 8 * 86400,
    )

    kwargs = await _run_one_tick(mock_agent, state_path)
    assert kwargs is not None
    assert "Tier: weekly" in kwargs["system_task_prompt"]
    assert kwargs["session_id"].startswith("system:nudge:weekly:")

    state = json.loads(state_path.read_text())
    assert state["last_weekly_at"] > time.time() - 60


@pytest.mark.asyncio
async def test_weekly_skipped_when_not_due(mock_agent, tmp_path):
    state_path = tmp_path / "nudge_state.json"
    _write_state(
        state_path,
        last_user_msg_at=time.time() - 3600,
        last_weekly_at=time.time() - 3 * 86400,
    )

    kwargs = await _run_one_tick(mock_agent, state_path)
    assert kwargs is None


@pytest.mark.asyncio
async def test_ladder_takes_precedence_over_weekly(mock_agent, tmp_path):
    """Idle 3d + weekly due: fire 2d, not weekly."""
    state_path = tmp_path / "nudge_state.json"
    _write_state(
        state_path,
        last_user_msg_at=time.time() - 3 * 86400,
        last_weekly_at=time.time() - 10 * 86400,
    )

    kwargs = await _run_one_tick(mock_agent, state_path)
    assert kwargs is not None
    assert "Tier: 2d" in kwargs["system_task_prompt"]

    state = json.loads(state_path.read_text())
    assert state["last_weekly_at"] < time.time() - 9 * 86400


@pytest.mark.asyncio
async def test_disabled_flag_suppresses_fire(mock_agent, tmp_path):
    from run import periodic_nudge

    state_path = tmp_path / "nudge_state.json"
    _write_state(state_path, last_user_msg_at=time.time() - 30 * 86400)

    task = asyncio.create_task(periodic_nudge(
        mock_agent,
        interval_sec=0,
        state_path=state_path,
        recipient="user@example.com",
        enabled=False,
    ))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert not mock_agent.handle.called


@pytest.mark.asyncio
async def test_concurrent_user_reply_during_handle_not_clobbered(mock_agent, tmp_path):
    """If the user replies (state file rewritten) while agent.handle is
    awaiting, the post-handle save must not overwrite the fresh state."""
    from run import periodic_nudge

    state_path = tmp_path / "nudge_state.json"
    _write_state(state_path, last_user_msg_at=time.time() - 3 * 86400)

    fresh_reply_time = time.time()

    async def simulate_user_reply(**kwargs):
        # Mid-handle, the user replies and agent_worker writes fresh state.
        _write_state(
            state_path,
            last_user_msg_at=fresh_reply_time,
            tiers_sent=[],
            last_weekly_at=0.0,
        )

    mock_agent.handle.side_effect = simulate_user_reply

    task = asyncio.create_task(periodic_nudge(
        mock_agent,
        interval_sec=0,
        state_path=state_path,
        recipient="user@example.com",
        enabled=True,
    ))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    final = json.loads(state_path.read_text())
    # The fresh reply timestamp must be preserved, NOT the stale 3-day-ago one.
    assert abs(final["last_user_msg_at"] - fresh_reply_time) < 1.0
    # The ladder must remain empty (idle period reset by the reply).
    assert final["tiers_sent_this_idle"] == []
