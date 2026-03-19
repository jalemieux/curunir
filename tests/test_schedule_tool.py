# tests/test_schedule_tool.py
import json

import pytest

from src.tools.schedule_tool import exec_schedule


@pytest.fixture
def schedule_file(tmp_path, agent_config):
    """Point agent_config.context_dir at tmp_path with an empty schedules.json."""
    sf = tmp_path / "schedules.json"
    sf.write_text("[]")
    agent_config.context_dir = tmp_path
    return sf


class TestScheduleAdd:
    def test_add_task(self, agent_config, schedule_file):
        result = exec_schedule({
            "action": "add",
            "id": "morning-brief",
            "cron": "0 9 * * *",
            "prompt": "Check GitHub notifications.",
        }, agent_config)
        assert "added" in result.lower()
        tasks = json.loads(schedule_file.read_text())
        assert len(tasks) == 1
        assert tasks[0]["id"] == "morning-brief"
        assert tasks[0]["cron"] == "0 9 * * *"
        assert tasks[0]["prompt"] == "Check GitHub notifications."
        assert tasks[0]["skill"] is None
        assert tasks[0]["enabled"] is True
        assert tasks[0]["last_run"] == 0

    def test_add_with_skill(self, agent_config, schedule_file):
        result = exec_schedule({
            "action": "add",
            "id": "pr-review",
            "cron": "*/30 * * * *",
            "prompt": "Review open PRs.",
            "skill": "deep-research",
        }, agent_config)
        tasks = json.loads(schedule_file.read_text())
        assert tasks[0]["skill"] == "deep-research"

    def test_add_duplicate_id_fails(self, agent_config, schedule_file):
        exec_schedule({"action": "add", "id": "t1", "cron": "* * * * *", "prompt": "p"}, agent_config)
        result = exec_schedule({"action": "add", "id": "t1", "cron": "* * * * *", "prompt": "p2"}, agent_config)
        assert "already exists" in result.lower()

    def test_add_invalid_cron_fails(self, agent_config, schedule_file):
        result = exec_schedule({
            "action": "add", "id": "bad", "cron": "not a cron", "prompt": "p",
        }, agent_config)
        assert "invalid" in result.lower()

    def test_add_missing_fields_fails(self, agent_config, schedule_file):
        result = exec_schedule({"action": "add", "id": "t1"}, agent_config)
        assert "missing" in result.lower() or "required" in result.lower()


class TestScheduleList:
    def test_list_empty(self, agent_config, schedule_file):
        result = exec_schedule({"action": "list"}, agent_config)
        assert "no scheduled tasks" in result.lower()

    def test_list_with_tasks(self, agent_config, schedule_file):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p1"}, agent_config)
        exec_schedule({"action": "add", "id": "t2", "cron": "0 17 * * *", "prompt": "p2"}, agent_config)
        result = exec_schedule({"action": "list"}, agent_config)
        assert "t1" in result
        assert "t2" in result


class TestScheduleUpdate:
    def test_update_cron(self, agent_config, schedule_file):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        result = exec_schedule({"action": "update", "id": "t1", "cron": "0 10 * * *"}, agent_config)
        assert "updated" in result.lower()
        tasks = json.loads(schedule_file.read_text())
        assert tasks[0]["cron"] == "0 10 * * *"

    def test_update_prompt(self, agent_config, schedule_file):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "old"}, agent_config)
        exec_schedule({"action": "update", "id": "t1", "prompt": "new"}, agent_config)
        tasks = json.loads(schedule_file.read_text())
        assert tasks[0]["prompt"] == "new"

    def test_update_enabled(self, agent_config, schedule_file):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        exec_schedule({"action": "update", "id": "t1", "enabled": False}, agent_config)
        tasks = json.loads(schedule_file.read_text())
        assert tasks[0]["enabled"] is False

    def test_update_nonexistent_fails(self, agent_config, schedule_file):
        result = exec_schedule({"action": "update", "id": "nope", "cron": "* * * * *"}, agent_config)
        assert "not found" in result.lower()

    def test_update_invalid_cron_fails(self, agent_config, schedule_file):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        result = exec_schedule({"action": "update", "id": "t1", "cron": "bad"}, agent_config)
        assert "invalid" in result.lower()


class TestScheduleRemove:
    def test_remove_task(self, agent_config, schedule_file):
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        result = exec_schedule({"action": "remove", "id": "t1"}, agent_config)
        assert "removed" in result.lower()
        tasks = json.loads(schedule_file.read_text())
        assert len(tasks) == 0

    def test_remove_nonexistent_fails(self, agent_config, schedule_file):
        result = exec_schedule({"action": "remove", "id": "nope"}, agent_config)
        assert "not found" in result.lower()


class TestScheduleFileCreation:
    def test_creates_file_if_missing(self, tmp_path, agent_config):
        agent_config.context_dir = tmp_path
        # No schedules.json exists yet
        result = exec_schedule({"action": "list"}, agent_config)
        assert "no scheduled tasks" in result.lower()

    def test_add_creates_file_if_missing(self, tmp_path, agent_config):
        agent_config.context_dir = tmp_path
        exec_schedule({"action": "add", "id": "t1", "cron": "0 9 * * *", "prompt": "p"}, agent_config)
        sf = tmp_path / "schedules.json"
        assert sf.exists()
        assert len(json.loads(sf.read_text())) == 1


class TestInvalidAction:
    def test_unknown_action(self, agent_config, schedule_file):
        result = exec_schedule({"action": "bogus"}, agent_config)
        assert "unknown" in result.lower() or "invalid" in result.lower()

    def test_missing_action(self, agent_config, schedule_file):
        result = exec_schedule({}, agent_config)
        assert "action" in result.lower()
