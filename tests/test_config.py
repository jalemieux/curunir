# tests/test_config.py
from pathlib import Path

from src.config import AgentConfig, EmailChannelConfig


def test_default_config():
    config = AgentConfig()
    assert config.model == "anthropic/claude-sonnet-4-20250514"
    assert config.max_iterations == 200
    assert config.identity_file == Path("./context/identity.md")
    assert config.skill_dirs == [Path("./skills"), Path("./context/skills")]
    assert config.vision_model is None
    assert config.main_model_supports_vision is False


def test_custom_config():
    config = AgentConfig(model="openai/gpt-4o", max_iterations=5)
    assert config.model == "openai/gpt-4o"
    assert config.max_iterations == 5


def test_vision_config():
    config = AgentConfig(
        vision_model="openai/gpt-4o-mini",
        main_model_supports_vision=True,
    )
    assert config.vision_model == "openai/gpt-4o-mini"
    assert config.main_model_supports_vision is True


def test_email_config_defaults():
    config = EmailChannelConfig()
    assert config.enabled is False
    assert config.api_key == ""
    assert config.inbox_id == ""
    assert config.api_base == "https://api.deadsimple.email"
    assert config.poll_interval_sec == 60
    assert config.allowed_senders == []
    assert config.restrict_outbound is True
    assert config.attachment_dir == "/tmp/attachments"
    assert config.state_file == Path("./context/email_state.json")
    assert config.spam_score_threshold == 5.0


def test_email_config_custom():
    config = EmailChannelConfig(
        enabled=True,
        api_key="ds-key-abc123",
        inbox_id="inbox-xyz",
        poll_interval_sec=30,
        allowed_senders=["alice@example.com"],
        restrict_outbound=False,
        attachment_dir="/data/attachments",
        spam_score_threshold=3.0,
    )
    assert config.enabled is True
    assert config.api_key == "ds-key-abc123"
    assert config.inbox_id == "inbox-xyz"
    assert config.poll_interval_sec == 30
    assert config.allowed_senders == ["alice@example.com"]
    assert config.restrict_outbound is False
    assert config.attachment_dir == "/data/attachments"
    assert config.spam_score_threshold == 3.0


def test_persona_fields_default_to_none_and_path():
    c = AgentConfig()
    assert c.persona is None
    assert c.skill_allowlist is None
    assert c.persona_prompt_dir == Path("./context/persona")
