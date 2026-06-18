# tests/test_schedule_store.py
"""Tests for the SQLite-backed schedule store (src/schedule_store)."""
import sqlite3

import pytest

from src.schedule_store import db as sdb
from src.schedule_store import engine


def _fresh(tmp_path):
    path = str(tmp_path / "schedules.db")
    sdb.init_db(path)
    return path


class TestInit:
    def test_init_db_creates_table(self, tmp_path):
        path = _fresh(tmp_path)
        con = sqlite3.connect(path)
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert "schedules" in names

    def test_init_db_idempotent(self, tmp_path):
        path = _fresh(tmp_path)
        sdb.init_db(path)  # second call must not raise
        assert engine.list_schedules(path) == []


class TestCreate:
    def test_create_persists_row(self, tmp_path):
        path = _fresh(tmp_path)
        engine.create(path, {"id": "t1", "cron": "0 9 * * *", "prompt": "do it"})
        rows = engine.list_schedules(path)
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == "t1"
        assert row["cron"] == "0 9 * * *"
        assert row["prompt"] == "do it"
        assert row["skill"] is None
        assert row["enabled"] is True
        assert row["last_run"] == 0

    def test_create_with_skill(self, tmp_path):
        path = _fresh(tmp_path)
        engine.create(path, {"id": "t1", "cron": "* * * * *", "prompt": "p", "skill": "deep-research"})
        assert engine.list_schedules(path)[0]["skill"] == "deep-research"

    def test_create_rejects_invalid_cron(self, tmp_path):
        path = _fresh(tmp_path)
        with pytest.raises(ValueError, match="invalid cron"):
            engine.create(path, {"id": "t1", "cron": "not a cron", "prompt": "p"})

    def test_create_rejects_duplicate_id(self, tmp_path):
        path = _fresh(tmp_path)
        engine.create(path, {"id": "t1", "cron": "* * * * *", "prompt": "p"})
        with pytest.raises(ValueError, match="already exists"):
            engine.create(path, {"id": "t1", "cron": "* * * * *", "prompt": "p2"})

    def test_create_rejects_skill_outside_allowlist(self, tmp_path):
        path = _fresh(tmp_path)
        with pytest.raises(ValueError, match="not allowed"):
            engine.create(
                path,
                {"id": "t1", "cron": "* * * * *", "prompt": "p", "skill": "forbidden"},
                skill_allowlist=["deep-research"],
            )

    def test_create_allows_skill_in_allowlist(self, tmp_path):
        path = _fresh(tmp_path)
        engine.create(
            path,
            {"id": "t1", "cron": "* * * * *", "prompt": "p", "skill": "deep-research"},
            skill_allowlist=["deep-research"],
        )
        assert engine.list_schedules(path)[0]["skill"] == "deep-research"

    def test_create_rejects_missing_required(self, tmp_path):
        path = _fresh(tmp_path)
        with pytest.raises(ValueError, match="missing"):
            engine.create(path, {"id": "t1", "cron": "* * * * *"})


class TestUpdate:
    def test_update_editable_fields(self, tmp_path):
        path = _fresh(tmp_path)
        engine.create(path, {"id": "t1", "cron": "0 9 * * *", "prompt": "old"})
        engine.update(path, "t1", {"cron": "0 10 * * *", "prompt": "new", "enabled": False})
        row = engine.list_schedules(path)[0]
        assert row["cron"] == "0 10 * * *"
        assert row["prompt"] == "new"
        assert row["enabled"] is False

    def test_update_rejects_invalid_cron(self, tmp_path):
        path = _fresh(tmp_path)
        engine.create(path, {"id": "t1", "cron": "0 9 * * *", "prompt": "p"})
        with pytest.raises(ValueError, match="invalid cron"):
            engine.update(path, "t1", {"cron": "bad"})

    def test_update_rejects_skill_outside_allowlist(self, tmp_path):
        path = _fresh(tmp_path)
        engine.create(path, {"id": "t1", "cron": "* * * * *", "prompt": "p"})
        with pytest.raises(ValueError, match="not allowed"):
            engine.update(path, "t1", {"skill": "forbidden"}, skill_allowlist=["deep-research"])

    def test_update_nonexistent_raises(self, tmp_path):
        path = _fresh(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            engine.update(path, "nope", {"cron": "* * * * *"})

    def test_update_does_not_touch_run_metadata(self, tmp_path):
        path = _fresh(tmp_path)
        engine.create(path, {"id": "t1", "cron": "* * * * *", "prompt": "p"})
        engine.mark_run(path, "t1", 999, "success")
        engine.update(path, "t1", {"prompt": "new"})
        row = engine.list_schedules(path)[0]
        assert row["prompt"] == "new"
        assert row["last_run"] == 999
        assert row["last_status"] == "success"


class TestDelete:
    def test_delete_removes_row(self, tmp_path):
        path = _fresh(tmp_path)
        engine.create(path, {"id": "t1", "cron": "* * * * *", "prompt": "p"})
        engine.delete(path, "t1")
        assert engine.list_schedules(path) == []

    def test_delete_nonexistent_raises(self, tmp_path):
        path = _fresh(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            engine.delete(path, "nope")


class TestToggle:
    def test_toggle_flips_enabled(self, tmp_path):
        path = _fresh(tmp_path)
        engine.create(path, {"id": "t1", "cron": "* * * * *", "prompt": "p"})
        engine.toggle(path, "t1")
        assert engine.list_schedules(path)[0]["enabled"] is False
        engine.toggle(path, "t1")
        assert engine.list_schedules(path)[0]["enabled"] is True

    def test_toggle_nonexistent_raises(self, tmp_path):
        path = _fresh(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            engine.toggle(path, "nope")


class TestRunMetadata:
    def test_mark_attempt_sets_only_attempt(self, tmp_path):
        path = _fresh(tmp_path)
        engine.create(path, {"id": "t1", "cron": "* * * * *", "prompt": "p"})
        engine.mark_attempt(path, "t1", 1234)
        row = engine.list_schedules(path)[0]
        assert row["last_attempt_at"] == 1234
        assert row["last_run"] == 0

    def test_mark_run_success(self, tmp_path):
        path = _fresh(tmp_path)
        engine.create(path, {"id": "t1", "cron": "* * * * *", "prompt": "p"})
        engine.mark_run(path, "t1", 5555, "success")
        row = engine.list_schedules(path)[0]
        assert row["last_run"] == 5555
        assert row["last_status"] == "success"
        assert row["last_error"] is None

    def test_mark_run_error(self, tmp_path):
        path = _fresh(tmp_path)
        engine.create(path, {"id": "t1", "cron": "* * * * *", "prompt": "p"})
        engine.mark_run(path, "t1", 0, "error", error="boom")
        row = engine.list_schedules(path)[0]
        assert row["last_status"] == "error"
        assert row["last_error"] == "boom"
        # last_run not advanced on error (caller passes existing value or 0)
        assert row["last_run"] == 0

    def test_mark_metadata_does_not_touch_editable_fields(self, tmp_path):
        path = _fresh(tmp_path)
        engine.create(path, {"id": "t1", "cron": "0 9 * * *", "prompt": "keep me"})
        engine.mark_attempt(path, "t1", 100)
        engine.mark_run(path, "t1", 200, "success")
        row = engine.list_schedules(path)[0]
        assert row["cron"] == "0 9 * * *"
        assert row["prompt"] == "keep me"
