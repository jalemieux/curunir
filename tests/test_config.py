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
    assert config.user == ""
    assert config.password == ""
    assert config.inbox == ""
    assert config.imap_host == "imap.fastmail.com"
    assert config.smtp_host == "smtp.fastmail.com"
    assert config.poll_interval_sec == 60
    assert config.allowed_senders == []
    assert config.restrict_outbound is True
    assert config.attachment_dir == "/tmp/attachments"
    assert config.state_file == Path("./context/email_state.json")
    assert config.spam_score_threshold == 5.0


def test_email_config_custom():
    config = EmailChannelConfig(
        enabled=True,
        user="jac@curunir.ai",
        password="app-pass",
        inbox="jac@curunir.ai",
        poll_interval_sec=30,
        allowed_senders=["alice@example.com"],
        restrict_outbound=False,
        attachment_dir="/data/attachments",
        spam_score_threshold=3.0,
    )
    assert config.enabled is True
    assert config.user == "jac@curunir.ai"
    assert config.password == "app-pass"
    assert config.inbox == "jac@curunir.ai"
    assert config.poll_interval_sec == 30
    assert config.allowed_senders == ["alice@example.com"]
    assert config.restrict_outbound is False
    assert config.attachment_dir == "/data/attachments"
    assert config.spam_score_threshold == 3.0


def test_persona_defaults():
    c = AgentConfig()
    assert c.persona == "default"
    assert c.skill_allowlist is None


# --- agent/container path layout (agents-and-containers phase 1) ----------

def test_bare_config_is_legacy_layout():
    """A bare AgentConfig keeps every historical literal and shared==context."""
    c = AgentConfig()
    assert c.shared_dir == c.context_dir == Path("./context")
    assert c.agent_name == "default"
    assert c.is_default is True
    assert c.portfolio_db == Path("./context/memory/portfolio.db")
    assert c.crm_db == Path("./context/memory/crm.db")
    assert c.usage_db == Path("./context/usage.db")
    assert c.path_vars == {"context": "context", "shared": "context"}


def test_for_agent_legacy_equals_bare_field_for_field():
    import dataclasses

    bare = AgentConfig()
    derived = AgentConfig.for_agent("default", "./context")
    for f in dataclasses.fields(AgentConfig):
        assert getattr(bare, f.name) == getattr(derived, f.name), f.name


def test_for_agent_derives_private_and_shared_paths(tmp_path):
    ctx = tmp_path / "agents" / "finance"
    c = AgentConfig.for_agent("finance", ctx, tmp_path, is_default=False)
    assert c.agent_name == "finance"
    assert c.is_default is False
    assert c.context_dir == ctx
    assert c.shared_dir == tmp_path
    # private, from context_dir
    assert c.identity_file == ctx / "identity.md"
    assert c.schedules_db == ctx / "schedules.db"
    assert c.portfolio_db == ctx / "memory" / "portfolio.db"
    assert c.crm_db == ctx / "memory" / "crm.db"
    assert c.skill_dirs == [Path("./skills"), ctx / "skills"]
    # shared, from shared_dir
    assert c.usage_db == tmp_path / "usage.db"
    assert c.path_vars == {"context": str(ctx), "shared": str(tmp_path)}


def test_for_agent_shared_dir_defaults_to_context_dir(tmp_path):
    c = AgentConfig.for_agent("solo", tmp_path)
    assert c.shared_dir == tmp_path
    assert c.usage_db == tmp_path / "usage.db"


def test_for_agent_overrides_win(tmp_path):
    c = AgentConfig.for_agent(
        "finance", tmp_path, model="openai/gpt-4o",
        portfolio_db=tmp_path / "elsewhere.db",
    )
    assert c.model == "openai/gpt-4o"
    assert c.portfolio_db == tmp_path / "elsewhere.db"
    # untouched derivations still hold
    assert c.crm_db == tmp_path / "memory" / "crm.db"
