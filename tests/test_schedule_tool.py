# tests/test_schedule_tool.py
import pytest

from src.schedule_store import engine
from src.tools.schedule_tool import exec_schedule


@pytest.fixture
def schedule_db(tmp_path, agent_config):
    """Point agent_config.schedules_db at a tmp SQLite store."""
    db = tmp_path / "schedules.db"
    agent_config.schedules_db = db
    return str(db)


def _tasks(db):
    return engine.list_schedules(db)


class TestScheduleAdd:
    def test_add_task(self, agent_config, schedule_db):
        result = exec_schedule({
            "action": "add",
            "id": "morning-brief",
            "cron": "0 9 * * *",
            "prompt": "Check GitHub notifications.",
        }, agent_config)
        assert "added" in result.lower()
        tasks = _tasks(schedule_db)
        assert len(tasks) == 1
        assert tasks[0]["id"] == "morning-brief"
        assert tasks[0]["cron"] == "0 9 * * *"
        assert tasks[0]["prompt"] == "Check GitHub notifications."
        assert tasks[0]["skill"] is None
        assert tasks[0]["enabled"] is True
        assert tasks[0]["last_run"] == 0

    def test_add_with_skill(self, agent_config, schedule_db):
        exec_schedule({
            "action": "add",
            "id": "pr-review",
            "cron": "*/30 * * * *",
            "prompt": "Review open PRs.",
            "skill": "deep-research",
        }, agent_config)
        assert _tasks(schedule_db)[0]["skill"] == "deep-research"

    def test_add_duplicate_id_fails(self, agent_config, schedule_db):
        exec_schedule({"action": "add", "id": "t1", "cron": "* * * * *", "prompt": "p"}, agent_config)
        result = exec_schedule({"action": "add", "id": "t1", "cron": "* * * * *", "prompt": "p2"}, agent_config)
        assert "already exists" in result.lower()

    def test_add_invalid_cron_fails(self, agent_config, schedule_db):
        result = exec_schedule({
            "action": "add", "id": "bad", "cron": "not a cron", "prompt": "p",
        }, agent_config)
        assert "invalid" in result.lower()

    def test_add_missing_fields_fails(self, agent_config, schedule_db):
        result = exec_schedule({"action": "add", "id": "t1"}, agent_config)
        assert "missing" in result.lower() or "required" in result.lower()

    def test_add_skill_outside_allowlist_fails(self, agent_config, schedule_db):
        agent_config.skill_allowlist = ["deep-research"]
        result = exec_schedule({
            "action": "add", "id": "t1", "cron": "* * * * *",
            "prompt": "p", "skill": "forbidden",
        }, agent_config)
        assert "not allowed" in result.lower()
        assert _tasks(schedule_db) == []

    def test_add_skill_in_allowlist_succeeds(self, agent_config, schedule_db):
        agent_config.skill_allowlist = ["deep-research"]
        result = exec_schedule({
            "action": "add", "id": "t1", "cron": "* * * * *",
            "prompt": "p", "skill": "deep-research",
        }, agent_config)
        assert "added" in result.lower()


class TestScheduleList:
    def test_list_empty(self, agent_config, schedule_db):
        result = exec_schedule({"action": "list"}, agent_config)
        assert "no scheduled tasks" in result.lower()

    def test_list_with_tasks(self, agent_config, schedule_db):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p1"}, agent_config)
        exec_schedule({"action": "add", "id": "t2", "cron": "0 17 * * *", "prompt": "p2"}, agent_config)
        result = exec_schedule({"action": "list"}, agent_config)
        assert "t1" in result
        assert "t2" in result


class TestScheduleUpdate:
    def test_update_cron(self, agent_config, schedule_db):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        result = exec_schedule({"action": "update", "id": "t1", "cron": "0 10 * * *"}, agent_config)
        assert "updated" in result.lower()
        assert _tasks(schedule_db)[0]["cron"] == "0 10 * * *"

    def test_update_prompt(self, agent_config, schedule_db):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "old"}, agent_config)
        exec_schedule({"action": "update", "id": "t1", "prompt": "new"}, agent_config)
        assert _tasks(schedule_db)[0]["prompt"] == "new"

    def test_update_enabled(self, agent_config, schedule_db):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        exec_schedule({"action": "update", "id": "t1", "enabled": False}, agent_config)
        assert _tasks(schedule_db)[0]["enabled"] is False

    def test_update_nonexistent_fails(self, agent_config, schedule_db):
        result = exec_schedule({"action": "update", "id": "nope", "cron": "* * * * *"}, agent_config)
        assert "not found" in result.lower()

    def test_update_invalid_cron_fails(self, agent_config, schedule_db):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        result = exec_schedule({"action": "update", "id": "t1", "cron": "bad"}, agent_config)
        assert "invalid" in result.lower()


class TestScheduleRemove:
    def test_remove_task(self, agent_config, schedule_db):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        result = exec_schedule({"action": "remove", "id": "t1"}, agent_config)
        assert "removed" in result.lower()
        assert len(_tasks(schedule_db)) == 0

    def test_remove_nonexistent_fails(self, agent_config, schedule_db):
        result = exec_schedule({"action": "remove", "id": "nope"}, agent_config)
        assert "not found" in result.lower()


class TestScheduleToggle:
    def test_toggle_disables_then_enables(self, agent_config, schedule_db):
        exec_schedule({"action": "add", "id": "t1", "cron": "* * * * *", "prompt": "p"}, agent_config)
        result = exec_schedule({"action": "toggle", "id": "t1"}, agent_config)
        assert "disabled" in result.lower()
        assert _tasks(schedule_db)[0]["enabled"] is False
        result = exec_schedule({"action": "toggle", "id": "t1"}, agent_config)
        assert "enabled" in result.lower()
        assert _tasks(schedule_db)[0]["enabled"] is True

    def test_toggle_nonexistent_fails(self, agent_config, schedule_db):
        result = exec_schedule({"action": "toggle", "id": "nope"}, agent_config)
        assert "not found" in result.lower()


class TestScheduleStoreCreation:
    def test_list_creates_store_if_missing(self, tmp_path, agent_config):
        agent_config.schedules_db = tmp_path / "schedules.db"
        result = exec_schedule({"action": "list"}, agent_config)
        assert "no scheduled tasks" in result.lower()

    def test_add_creates_store_if_missing(self, tmp_path, agent_config):
        agent_config.schedules_db = tmp_path / "schedules.db"
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        assert (tmp_path / "schedules.db").exists()
        assert len(engine.list_schedules(str(tmp_path / "schedules.db"))) == 1


class TestInvalidAction:
    def test_unknown_action(self, agent_config, schedule_db):
        result = exec_schedule({"action": "bogus"}, agent_config)
        assert "unknown" in result.lower() or "invalid" in result.lower()

    def test_missing_action(self, agent_config, schedule_db):
        result = exec_schedule({}, agent_config)
        assert "action" in result.lower()
