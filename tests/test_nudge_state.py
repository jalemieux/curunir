"""Tests for src.nudge_state — small JSON state for the nudge engine."""
import json
import time

import pytest

from src.nudge_state import NudgeState


def test_load_creates_default_when_missing(tmp_path):
    """First load on a fresh install initializes last_user_msg_at to ~now
    so we don't immediately nudge."""
    path = tmp_path / "nudge_state.json"
    before = time.time()
    state = NudgeState.load(path)
    after = time.time()

    assert before <= state.last_user_msg_at <= after
    assert state.tiers_sent_this_idle == []
    assert state.last_weekly_at == 0


def test_save_and_reload_round_trip(tmp_path):
    path = tmp_path / "nudge_state.json"
    state = NudgeState.load(path)
    state.last_user_msg_at = 1700000000
    state.tiers_sent_this_idle = ["2d", "7d"]
    state.last_weekly_at = 1700100000
    state.save()

    reloaded = NudgeState.load(path)
    assert reloaded.last_user_msg_at == 1700000000
    assert reloaded.tiers_sent_this_idle == ["2d", "7d"]
    assert reloaded.last_weekly_at == 1700100000


def test_save_creates_parent_directories(tmp_path):
    """save() should mkdir -p the parent so first-run on a fresh install works."""
    path = tmp_path / "subdir" / "nested" / "nudge_state.json"
    assert not path.parent.exists()
    state = NudgeState.load(path)
    assert path.exists()
    # And the on-disk JSON is well-formed.
    data = json.loads(path.read_text())
    assert "last_user_msg_at" in data


def test_record_user_message_resets_ladder(tmp_path):
    path = tmp_path / "nudge_state.json"
    state = NudgeState.load(path)
    state.tiers_sent_this_idle = ["2d", "7d"]
    state.last_user_msg_at = 1
    state.save()

    NudgeState.record_user_message(path)

    reloaded = NudgeState.load(path)
    assert reloaded.tiers_sent_this_idle == []
    assert reloaded.last_user_msg_at > 1
