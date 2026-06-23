# tests/test_scheduler.py
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.schedule_store import db as sdb
from src.schedule_store import engine
from src.scheduler import _check_and_fire, _is_due, _run_task


@pytest.fixture
def schedule_db(tmp_path, agent_config):
    """Point agent_config.schedules_db at a fresh tmp SQLite store."""
    db = tmp_path / "schedules.db"
    agent_config.schedules_db = db
    sdb.init_db(str(db))
    return str(db)


def _seed(db, **overrides):
    fields = {"id": "t1", "cron": "* * * * *", "prompt": "do it"}
    fields.update({k: overrides.pop(k) for k in ("id", "cron", "prompt", "skill") if k in overrides})
    engine.create(db, fields)
    # Apply enabled/run-metadata overrides via dedicated writers.
    if "enabled" in overrides and not overrides["enabled"]:
        engine.toggle(db, fields["id"])
    if "last_run" in overrides:
        engine.mark_run(db, fields["id"], overrides["last_run"], "success")


class TestIsDue:
    def test_never_run_task_is_due(self):
        task = {"cron": "* * * * *", "last_run": 0}
        assert _is_due(task, time.time()) is True

    def test_recently_run_task_not_due(self):
        task = {"cron": "0 9 * * *", "last_run": int(time.time())}
        assert _is_due(task, time.time()) is False

    def test_last_run_in_future_not_due(self):
        task = {"cron": "* * * * *", "last_run": int(time.time()) + 3600}
        assert _is_due(task, time.time()) is False

    def test_invalid_cron_not_due(self):
        task = {"cron": "not valid", "last_run": 0}
        assert _is_due(task, time.time()) is False

    def test_hourly_task_due_after_one_hour(self):
        now = time.time()
        task = {"cron": "0 * * * *", "last_run": int(now) - 3601}
        assert _is_due(task, now) is True

    def test_recent_attempt_blocks_due_even_without_last_run(self):
        now = time.time()
        task = {"cron": "* * * * *", "last_run": 0, "last_attempt_at": int(now)}
        assert _is_due(task, now) is False

    def test_old_schedule_without_last_attempt_still_due(self):
        task = {"cron": "* * * * *", "last_run": 0}
        assert _is_due(task, time.time()) is True


class TestCheckAndFire:
    async def test_fires_due_task(self, schedule_db, agent_config):
        _seed(schedule_db)  # never run, every-minute cron → due
        mock_agent = AsyncMock()
        mock_agent.config = agent_config

        fired = await _check_and_fire(mock_agent)
        await asyncio.sleep(0)

        assert "t1" in fired
        mock_agent.handle.assert_called_once()
        call_kwargs = mock_agent.handle.call_args
        assert call_kwargs.kwargs["system_task_prompt"] == "do it"
        assert call_kwargs.kwargs["session_id"].startswith("sched:t1:")

        row = engine.list_schedules(schedule_db)[0]
        assert row["last_attempt_at"] > 0
        assert row["last_run"] > 0
        assert row["last_status"] == "success"
        assert row["last_error"] is None

    async def test_failed_task_does_not_advance_last_run(self, schedule_db, agent_config):
        _seed(schedule_db)
        mock_agent = AsyncMock()
        mock_agent.config = agent_config
        mock_agent.handle.side_effect = RuntimeError("kaboom")

        fired = await _check_and_fire(mock_agent)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert "t1" in fired
        row = engine.list_schedules(schedule_db)[0]
        assert row["last_attempt_at"] > 0
        assert row["last_run"] == 0
        assert row["last_status"] == "error"
        assert "kaboom" in row["last_error"]

    async def test_failed_task_truncates_long_error(self, schedule_db, agent_config):
        _seed(schedule_db)
        mock_agent = AsyncMock()
        mock_agent.config = agent_config
        mock_agent.handle.side_effect = RuntimeError("x" * 2000)

        await _check_and_fire(mock_agent)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        row = engine.list_schedules(schedule_db)[0]
        assert len(row["last_error"]) == 500

    async def test_in_flight_task_not_re_fired(self, schedule_db, agent_config):
        _seed(schedule_db)
        gate = asyncio.Event()

        async def slow_handle(**kwargs):
            await gate.wait()

        mock_agent = AsyncMock()
        mock_agent.config = agent_config
        mock_agent.handle.side_effect = slow_handle

        fired1 = await _check_and_fire(mock_agent)
        await asyncio.sleep(0)
        fired2 = await _check_and_fire(mock_agent)

        assert fired1 == ["t1"]
        assert fired2 == []
        assert mock_agent.handle.call_count == 1

        gate.set()
        await asyncio.sleep(0)

    async def test_skips_disabled_task(self, schedule_db, agent_config):
        _seed(schedule_db, enabled=False)
        mock_agent = AsyncMock()
        mock_agent.config = agent_config

        fired = await _check_and_fire(mock_agent)

        assert fired == []
        mock_agent.handle.assert_not_called()

    async def test_skips_recently_run_task(self, schedule_db, agent_config):
        _seed(schedule_db, last_run=int(time.time()))
        mock_agent = AsyncMock()
        mock_agent.config = agent_config

        fired = await _check_and_fire(mock_agent)

        assert fired == []
        mock_agent.handle.assert_not_called()

    async def test_handles_corrupt_store(self, tmp_path, agent_config):
        # A non-SQLite file at the store path must not crash the tick.
        bad = tmp_path / "schedules.db"
        bad.write_text("not a database")
        agent_config.schedules_db = bad
        mock_agent = AsyncMock()
        mock_agent.config = agent_config

        fired = await _check_and_fire(mock_agent)

        assert fired == []

    async def test_handles_missing_store(self, tmp_path, agent_config):
        agent_config.schedules_db = tmp_path / "schedules.db"  # not created
        mock_agent = AsyncMock()
        mock_agent.config = agent_config

        fired = await _check_and_fire(mock_agent)

        assert fired == []

    async def test_edit_picked_up_without_restart(self, schedule_db, agent_config):
        # No task yet → nothing fires. Write one via the store, then the next
        # tick (same process, no restart) picks it up.
        mock_agent = AsyncMock()
        mock_agent.config = agent_config

        assert await _check_and_fire(mock_agent) == []
        engine.create(schedule_db, {"id": "t1", "cron": "* * * * *", "prompt": "do it"})
        fired = await _check_and_fire(mock_agent)
        await asyncio.sleep(0)

        assert fired == ["t1"]

    async def test_loads_skill_into_prompt(self, schedule_db, agent_config, tmp_skills):
        skill_dir = tmp_skills / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: test\n---\nDo special things.")

        _seed(schedule_db, skill="my-skill")
        mock_agent = AsyncMock()
        mock_agent.config = agent_config

        await _check_and_fire(mock_agent)
        await asyncio.sleep(0)

        prompt = mock_agent.handle.call_args.kwargs["system_task_prompt"]
        assert "Do special things." in prompt
        assert "do it" in prompt

    async def test_fires_multiple_tasks_concurrently(self, schedule_db, agent_config):
        engine.create(schedule_db, {"id": "t1", "cron": "* * * * *", "prompt": "p1"})
        engine.create(schedule_db, {"id": "t2", "cron": "* * * * *", "prompt": "p2"})
        mock_agent = AsyncMock()
        mock_agent.config = agent_config

        fired = await _check_and_fire(mock_agent)
        await asyncio.sleep(0)

        assert "t1" in fired
        assert "t2" in fired
        assert mock_agent.handle.call_count == 2


class TestEscalationHook:
    async def test_failure_invokes_escalation_hook(self, schedule_db, agent_config):
        engine.create(schedule_db, {"id": "t1", "cron": "* * * * *", "prompt": "do it"})
        mock_agent = AsyncMock()
        mock_agent.config = agent_config
        mock_agent.handle.side_effect = RuntimeError("kaboom")
        hook = MagicMock()
        mock_agent.on_schedule_failure = hook

        await _run_task(mock_agent, agent_config, "t1", "sched:t1:1", "do it")

        hook.assert_called_once()
        assert hook.call_args.args[0] == "t1"
        assert "kaboom" in hook.call_args.args[1]

    async def test_failure_still_marks_error(self, schedule_db, agent_config):
        engine.create(schedule_db, {"id": "t1", "cron": "* * * * *", "prompt": "do it"})
        mock_agent = AsyncMock()
        mock_agent.config = agent_config
        mock_agent.handle.side_effect = RuntimeError("kaboom")
        mock_agent.on_schedule_failure = MagicMock()

        await _run_task(mock_agent, agent_config, "t1", "sched:t1:1", "do it")

        row = engine.list_schedules(schedule_db)[0]
        assert row["last_status"] == "error"
        assert "kaboom" in row["last_error"]

    async def test_no_hook_is_unchanged(self, schedule_db, agent_config):
        engine.create(schedule_db, {"id": "t1", "cron": "* * * * *", "prompt": "do it"})
        mock_agent = AsyncMock()
        mock_agent.config = agent_config
        mock_agent.handle.side_effect = RuntimeError("kaboom")
        mock_agent.on_schedule_failure = None  # no hook configured

        # Must not raise even though no hook is present.
        await _run_task(mock_agent, agent_config, "t1", "sched:t1:1", "do it")

        row = engine.list_schedules(schedule_db)[0]
        assert row["last_status"] == "error"

    async def test_hook_failure_does_not_break_task(self, schedule_db, agent_config):
        engine.create(schedule_db, {"id": "t1", "cron": "* * * * *", "prompt": "do it"})
        mock_agent = AsyncMock()
        mock_agent.config = agent_config
        mock_agent.handle.side_effect = RuntimeError("kaboom")
        mock_agent.on_schedule_failure = MagicMock(side_effect=ValueError("hook boom"))

        # A throwing hook must not propagate out of _run_task.
        await _run_task(mock_agent, agent_config, "t1", "sched:t1:1", "do it")

        row = engine.list_schedules(schedule_db)[0]
        assert row["last_status"] == "error"

    async def test_success_does_not_invoke_hook(self, schedule_db, agent_config):
        engine.create(schedule_db, {"id": "t1", "cron": "* * * * *", "prompt": "do it"})
        mock_agent = AsyncMock()
        mock_agent.config = agent_config
        hook = MagicMock()
        mock_agent.on_schedule_failure = hook

        await _run_task(mock_agent, agent_config, "t1", "sched:t1:1", "do it")

        hook.assert_not_called()
