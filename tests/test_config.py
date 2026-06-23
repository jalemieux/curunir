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


def test_state_paths_derive_from_default_context_dir():
    """The default config keeps the historical ./context layout."""
    c = AgentConfig()
    assert c.context_dir == Path("./context")
    assert c.identity_file == Path("./context/identity.md")
    assert c.usage_db == Path("./context/usage.db")
    assert c.schedules_db == Path("./context/schedules.db")
    assert Path(c.portfolio_db) == Path("./context/memory/portfolio.db")
    # FS-tool sandbox root defaults under the persona's context dir.
    assert c.workdir == Path("./context/workspace")
    assert c.fs_jail is False


def test_context_dir_swap_rederives_every_state_path():
    """Swapping context_dir forks ALL per-persona state onto the new root.

    This is the prerequisite that lets one process host one context dir per
    persona — every stored artifact derives from context_dir.
    """
    c = AgentConfig(context_dir=Path("/srv/context/finance"))
    root = Path("/srv/context/finance")
    assert c.identity_file == root / "identity.md"
    assert c.usage_db == root / "usage.db"
    assert c.schedules_db == root / "schedules.db"
    assert Path(c.portfolio_db) == root / "memory" / "portfolio.db"
    assert c.workdir == root / "workspace"


def test_explicit_state_paths_override_derivation():
    """Explicitly-passed paths win over context_dir derivation."""
    c = AgentConfig(
        context_dir=Path("/srv/context/finance"),
        usage_db=Path("/custom/usage.db"),
        workdir=Path("/custom/work"),
    )
    assert c.usage_db == Path("/custom/usage.db")
    assert c.workdir == Path("/custom/work")
    # Non-overridden ones still derive from context_dir.
    assert c.schedules_db == Path("/srv/context/finance/schedules.db")


def test_fs_jail_opt_in():
    c = AgentConfig(context_dir=Path("/srv/context/finance"), fs_jail=True)
    assert c.fs_jail is True
    assert c.workdir == Path("/srv/context/finance/workspace")
