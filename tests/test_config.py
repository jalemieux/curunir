# tests/test_config.py
from pathlib import Path

from src.config import AgentConfig, EmailChannelConfig


def test_default_config():
    config = AgentConfig()
    assert config.model == "anthropic/claude-sonnet-4-20250514"
    assert config.max_iterations == 75
    assert config.identity_file == Path("./context/identity.md")
    assert config.skills_dir == Path("./skills")


def test_agent_config_defaults():
    cfg = AgentConfig()
    assert cfg.max_tokens == 16_000
    assert cfg.n_ctx is None
    assert not hasattr(cfg, "max_history_chars")


def test_custom_config():
    config = AgentConfig(model="openai/gpt-4o", max_iterations=5)
    assert config.model == "openai/gpt-4o"
    assert config.max_iterations == 5


def test_email_config_defaults():
    config = EmailChannelConfig()
    assert config.enabled is False
    assert config.service_account_file == ""
    assert config.delegated_user == ""
    assert config.poll_interval_sec == 60
    assert config.allowed_senders == []
    assert config.processed_label == "agent/processed"
    assert config.attachment_dir == "/tmp/attachments"


def test_email_config_custom():
    config = EmailChannelConfig(
        enabled=True,
        service_account_file="/secrets/key.json",
        delegated_user="bot@example.com",
        poll_interval_sec=30,
        allowed_senders=["alice@example.com"],
        processed_label="custom/done",
        attachment_dir="/data/attachments",
    )
    assert config.enabled is True
    assert config.service_account_file == "/secrets/key.json"
    assert config.delegated_user == "bot@example.com"
    assert config.poll_interval_sec == 30
    assert config.allowed_senders == ["alice@example.com"]
    assert config.processed_label == "custom/done"
    assert config.attachment_dir == "/data/attachments"
