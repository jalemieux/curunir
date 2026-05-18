# tests/test_reengagement.py
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.reengagement import (
    ReengagementConfig,
    ReengagementJob,
    load_activity,
    mark_nudge_sent,
    record_interaction,
    select_series,
    should_nudge,
)

DAY = 86400


def _config(**overrides) -> ReengagementConfig:
    base = dict(
        enabled=True,
        email_enabled=True,
        owner_email="owner@example.com",
        activation_window_days=14,
        activation_thresholds=(2, 5, 10),
        reengagement_thresholds=(7, 14, 21),
    )
    base.update(overrides)
    return ReengagementConfig(**base)


# --- activity store -------------------------------------------------------


class TestActivityStore:
    def test_load_missing_file_returns_empty(self, agent_config):
        assert load_activity(agent_config) == {}

    def test_record_interaction_round_trip(self, agent_config):
        record_interaction(agent_config, now=1000.0)
        data = load_activity(agent_config)
        assert data["created_at"] == 1000.0
        assert data["last_interaction_at"] == 1000.0
        assert data["nudges_sent"] == 0
        # The file on disk is valid JSON.
        raw = json.loads((agent_config.context_dir / "activity.json").read_text())
        assert raw == data

    def test_record_interaction_preserves_created_at(self, agent_config):
        record_interaction(agent_config, now=1000.0)
        record_interaction(agent_config, now=5000.0)
        data = load_activity(agent_config)
        assert data["created_at"] == 1000.0
        assert data["last_interaction_at"] == 5000.0

    def test_record_interaction_rearms_counter(self, agent_config):
        record_interaction(agent_config, now=1000.0)
        mark_nudge_sent(agent_config, now=2000.0)
        mark_nudge_sent(agent_config, now=3000.0)
        assert load_activity(agent_config)["nudges_sent"] == 2
        # Owner comes back — counter resets.
        record_interaction(agent_config, now=4000.0)
        assert load_activity(agent_config)["nudges_sent"] == 0

    def test_mark_nudge_sent_increments(self, agent_config):
        record_interaction(agent_config, now=1000.0)
        mark_nudge_sent(agent_config, now=2000.0)
        data = load_activity(agent_config)
        assert data["nudges_sent"] == 1
        assert data["last_nudge_at"] == 2000.0


# --- series selection -----------------------------------------------------


class TestSelectSeries:
    def test_young_account_is_activation(self):
        now = 100 * DAY
        assert select_series(now - 5 * DAY, now, 14) == "activation"

    def test_old_account_is_reengagement(self):
        now = 100 * DAY
        assert select_series(now - 30 * DAY, now, 14) == "reengagement"

    def test_boundary_at_window_is_activation(self):
        now = 100 * DAY
        # Exactly 14 days old → still activation (<= window).
        assert select_series(now - 14 * DAY, now, 14) == "activation"

    def test_just_past_boundary_is_reengagement(self):
        now = 100 * DAY
        assert select_series(now - 14 * DAY - 1, now, 14) == "reengagement"


# --- should_nudge gating --------------------------------------------------


class TestShouldNudgeGates:
    def test_feature_disabled(self):
        decision, _, reason = should_nudge({}, 0, _config(enabled=False))
        assert decision == "SKIP"
        assert "disabled" in reason

    def test_email_disabled(self):
        decision, _, _ = should_nudge({}, 0, _config(email_enabled=False))
        assert decision == "SKIP"

    def test_no_owner_email(self):
        decision, _, _ = should_nudge({}, 0, _config(owner_email=""))
        assert decision == "SKIP"

    def test_no_baseline(self):
        decision, series, reason = should_nudge({}, 0, _config())
        assert decision == "SKIP"
        assert series is None
        assert "baseline" in reason


class TestShouldNudgeActivation:
    def test_below_first_threshold_skips(self):
        now = 5 * DAY
        activity = {"created_at": now - 1 * DAY, "last_interaction_at": now - 1 * DAY,
                    "nudges_sent": 0}
        decision, series, _ = should_nudge(activity, now, _config())
        assert decision == "SKIP"
        assert series == "activation"

    def test_first_threshold_fires_at_2_days(self):
        now = 5 * DAY
        activity = {"created_at": now - 3 * DAY, "last_interaction_at": now - 2 * DAY,
                    "nudges_sent": 0}
        decision, series, _ = should_nudge(activity, now, _config())
        assert decision == "GO"
        assert series == "activation"

    def test_second_threshold_needs_5_days(self):
        now = 20 * DAY
        # 3 quiet days, one nudge already sent → next threshold is 5d.
        activity = {"created_at": now - 4 * DAY, "last_interaction_at": now - 3 * DAY,
                    "nudges_sent": 1}
        assert should_nudge(activity, now, _config())[0] == "SKIP"
        activity["last_interaction_at"] = now - 5 * DAY
        assert should_nudge(activity, now, _config())[0] == "GO"

    def test_counter_exhausted_skips(self):
        now = 30 * DAY
        activity = {"created_at": now - 1 * DAY, "last_interaction_at": now - 20 * DAY,
                    "nudges_sent": 3}
        decision, series, reason = should_nudge(activity, now, _config())
        assert decision == "SKIP"
        assert "cap" in reason


class TestShouldNudgeReengagement:
    def test_thresholds_7_14_21(self):
        now = 100 * DAY
        created = now - 60 * DAY  # well past the activation window
        for sent, quiet, expected in [
            (0, 6, "SKIP"), (0, 7, "GO"),
            (1, 13, "SKIP"), (1, 14, "GO"),
            (2, 20, "SKIP"), (2, 21, "GO"),
            (3, 99, "SKIP"),
        ]:
            activity = {"created_at": created,
                        "last_interaction_at": now - quiet * DAY,
                        "nudges_sent": sent}
            decision, series, _ = should_nudge(activity, now, _config())
            assert decision == expected, (sent, quiet)
            if series is not None:
                assert series == "reengagement"


# --- ReengagementConfig.from_env -----------------------------------------


class TestConfigFromEnv:
    def test_defaults(self, monkeypatch):
        for k in ("REENGAGEMENT_ENABLED", "EMAIL_ENABLED", "REENGAGEMENT_OWNER_EMAIL",
                  "EMAIL_ALLOWED_SENDERS", "ACTIVATION_THRESHOLDS",
                  "REENGAGEMENT_THRESHOLDS", "REENGAGEMENT_CRON",
                  "ACTIVATION_WINDOW_DAYS"):
            monkeypatch.delenv(k, raising=False)
        cfg = ReengagementConfig.from_env()
        assert cfg.enabled is False
        assert cfg.activation_thresholds == (2, 5, 10)
        assert cfg.reengagement_thresholds == (7, 14, 21)

    def test_owner_falls_back_to_allowed_senders(self, monkeypatch):
        monkeypatch.delenv("REENGAGEMENT_OWNER_EMAIL", raising=False)
        monkeypatch.setenv("EMAIL_ALLOWED_SENDERS", "first@example.com, second@example.com")
        assert ReengagementConfig.from_env().owner_email == "first@example.com"

    def test_explicit_owner_wins(self, monkeypatch):
        monkeypatch.setenv("REENGAGEMENT_OWNER_EMAIL", "owner@example.com")
        monkeypatch.setenv("EMAIL_ALLOWED_SENDERS", "other@example.com")
        assert ReengagementConfig.from_env().owner_email == "owner@example.com"

    def test_custom_thresholds(self, monkeypatch):
        monkeypatch.setenv("ACTIVATION_THRESHOLDS", "1, 3, 6")
        assert ReengagementConfig.from_env().activation_thresholds == (1, 3, 6)

    def test_malformed_thresholds_fall_back(self, monkeypatch):
        monkeypatch.setenv("REENGAGEMENT_THRESHOLDS", "not,ints")
        assert ReengagementConfig.from_env().reengagement_thresholds == (7, 14, 21)


# --- ReengagementJob integration -----------------------------------------


def _agent(agent_config):
    return SimpleNamespace(config=agent_config)


class TestReengagementJob:
    def test_job_cron_defaults_to_config(self):
        job = ReengagementJob(_config(cron="0 9 * * *"))
        assert job.cron == "0 9 * * *"
        assert job.id == "reengagement-nudge"

    async def test_go_path_sends_then_marks(self, agent_config):
        now = time.time()
        record_interaction(agent_config, now=now - 30 * DAY)
        # Age the account past the activation window and clear nudges.
        data = load_activity(agent_config)
        data["created_at"] = now - 60 * DAY
        (agent_config.context_dir / "activity.json").write_text(json.dumps(data))

        mock_client = AsyncMock()
        with patch("src.reengagement.call_llm",
                   new=AsyncMock(return_value=SimpleNamespace(text="Hi, checking in!"))), \
             patch("src.reengagement.build_client_from_env", return_value=mock_client):
            await ReengagementJob(_config()).run(_agent(agent_config))

        mock_client.send_email.assert_awaited_once()
        kwargs = mock_client.send_email.call_args.kwargs
        assert kwargs["to"] == "owner@example.com"
        assert kwargs["text_body"] == "Hi, checking in!"
        assert load_activity(agent_config)["nudges_sent"] == 1

    async def test_skip_path_sends_nothing(self, agent_config):
        now = time.time()
        record_interaction(agent_config, now=now)  # active just now → SKIP

        mock_client = AsyncMock()
        with patch("src.reengagement.call_llm", new=AsyncMock()) as mock_llm, \
             patch("src.reengagement.build_client_from_env", return_value=mock_client):
            await ReengagementJob(_config()).run(_agent(agent_config))

        mock_llm.assert_not_awaited()
        mock_client.send_email.assert_not_awaited()
        assert load_activity(agent_config).get("nudges_sent") == 0

    async def test_send_failure_leaves_counter_unchanged(self, agent_config):
        now = time.time()
        record_interaction(agent_config, now=now - 30 * DAY)
        data = load_activity(agent_config)
        data["created_at"] = now - 60 * DAY
        (agent_config.context_dir / "activity.json").write_text(json.dumps(data))

        mock_client = AsyncMock()
        mock_client.send_email.side_effect = RuntimeError("smtp down")
        with patch("src.reengagement.call_llm",
                   new=AsyncMock(return_value=SimpleNamespace(text="body"))), \
             patch("src.reengagement.build_client_from_env", return_value=mock_client):
            with pytest.raises(RuntimeError):
                await ReengagementJob(_config()).run(_agent(agent_config))

        # Not marked → retried next tick.
        assert load_activity(agent_config)["nudges_sent"] == 0
        mock_client.aclose.assert_awaited_once()
