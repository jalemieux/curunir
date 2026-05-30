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
    awaiting, the post-handle save must not re-add a tier the reply cleared."""
    from run import periodic_nudge

    state_path = tmp_path / "nudge_state.json"
    _write_state(state_path, last_user_msg_at=time.time() - 3 * 86400)

    fresh_reply_time = None
    call_count = 0

    async def simulate_user_reply(**kwargs):
        nonlocal call_count, fresh_reply_time
        call_count += 1
        if call_count == 1:
            # Mid-handle, user replies — agent_worker writes fresh state.
            # Capture the reply timestamp HERE (after the loop's `now`) so the
            # post-handle reload sees last_user_msg_at > now, just like a real
            # concurrent record_user_message write would.
            fresh_reply_time = time.time()
            _write_state(
                state_path,
                last_user_msg_at=fresh_reply_time,
                tiers_sent=[],
                last_weekly_at=0.0,
            )
        else:
            # Stop the loop so subsequent ticks don't mask the first-tick result.
            raise asyncio.CancelledError()

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
    # Fresh reply timestamp preserved, not the stale 3-day-ago one.
    assert abs(final["last_user_msg_at"] - fresh_reply_time) < 1.0
    # Ladder remains empty — the 2d tier fired during the OLD idle period;
    # the user reply started a new idle period and we must not pre-mark it.
    assert final["tiers_sent_this_idle"] == []


@pytest.mark.asyncio
async def test_agent_worker_records_inbound(tmp_path, agent_config):
    """Each IncomingMessage dequeued bumps last_user_msg_at and clears the ladder."""
    from unittest.mock import AsyncMock

    from run import agent_worker
    from src.channels.base import IncomingMessage

    state_path = tmp_path / "nudge_state.json"
    state_path.write_text(json.dumps({
        "last_user_msg_at": 1.0,
        "tiers_sent_this_idle": ["2d", "7d"],
        "last_weekly_at": 0.0,
    }))

    in_queue = asyncio.Queue()
    out_queue = asyncio.Queue()
    await in_queue.put(IncomingMessage(
        content="hello",
        session_id="cli",
        channel="ws",
        reply_address={},
    ))

    agent = AsyncMock()
    agent.config = agent_config
    agent.sessions = {}
    agent.handle.return_value = "ok"

    task = asyncio.create_task(agent_worker(agent, in_queue, out_queue, nudge_state_path=state_path))
    try:
        await asyncio.wait_for(out_queue.get(), timeout=2.0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    state = json.loads(state_path.read_text())
    assert state["last_user_msg_at"] > time.time() - 60
    assert state["tiers_sent_this_idle"] == []


@pytest.mark.asyncio
async def test_14d_consumes_lower_tiers_after_long_downtime(mock_agent, tmp_path):
    """If the system was offline >14d while user was idle, fire 14d once
    and consume 2d/7d so they don't fire in reverse order on later ticks."""
    state_path = tmp_path / "nudge_state.json"
    _write_state(state_path, last_user_msg_at=time.time() - 15 * 86400)

    kwargs = await _run_one_tick(mock_agent, state_path)
    assert kwargs is not None
    assert "Tier: 14d" in kwargs["system_task_prompt"]

    state = json.loads(state_path.read_text())
    assert set(state["tiers_sent_this_idle"]) == {"2d", "7d", "14d"}


@pytest.mark.asyncio
async def test_7d_consumes_2d(mock_agent, tmp_path):
    """Firing 7d (without prior 2d) also consumes 2d."""
    state_path = tmp_path / "nudge_state.json"
    _write_state(state_path, last_user_msg_at=time.time() - 8 * 86400)

    kwargs = await _run_one_tick(mock_agent, state_path)
    assert kwargs is not None
    assert "Tier: 7d" in kwargs["system_task_prompt"]

    state = json.loads(state_path.read_text())
    assert set(state["tiers_sent_this_idle"]) == {"2d", "7d"}
    assert "14d" not in state["tiers_sent_this_idle"]


@pytest.mark.asyncio
async def test_disabled_does_not_create_state_file(mock_agent, tmp_path):
    """When NUDGE_ENABLED=false, periodic_nudge must not write the state file."""
    from run import periodic_nudge

    state_path = tmp_path / "nudge_state.json"
    # Do NOT pre-create the state file.

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

    assert not state_path.exists()
    assert not mock_agent.handle.called
