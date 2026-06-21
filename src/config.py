# src/config.py
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentConfig:
    model: str = "anthropic/claude-sonnet-4-20250514"
    api_base: str | None = None
    openrouter_provider: str | None = None
    max_iterations: int = 200
    max_history_chars: int = 250_000
    max_tool_result_chars: int = 100_000
    identity_file: Path = Path("./context/identity.md")
    context_dir: Path = Path("./context")
    usage_db: Path = Path("./context/usage.db")
    schedules_db: Path = Path("./context/schedules.db")
    skill_dirs: list[Path] = field(
        default_factory=lambda: [Path("./skills"), Path("./context/skills")]
    )
    persona: str = "default"
    skill_allowlist: list[str] | None = None
    attachment_dir: str = "/tmp/attachments"
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"
    vision_model: str | None = None
    main_model_supports_vision: bool = False
    portfolio_db: str = "context/memory/portfolio.db"


@dataclass
class LocalWebConfig:
    """Settings for the loopback-bound local web console (LocalWebChannel).

    Operator-only surface: defaults to off and binds 127.0.0.1. Inside the
    Docker network the compose file overrides ``host`` to 0.0.0.0 — network
    isolation plus the shared ``context/.ws-token`` pairing token are the
    access controls there, mirroring ``WS_HOST``.
    """
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8766


@dataclass
class EmailChannelConfig:
    enabled: bool = False
    # Fastmail IMAP/SMTP transport. `inbox` is the From address (e.g.
    # jac@curunir.ai); `user`/`password` are the Fastmail login + app password.
    imap_host: str = "imap.fastmail.com"
    smtp_host: str = "smtp.fastmail.com"
    user: str = ""
    password: str = ""
    inbox: str = ""
    poll_interval_sec: int = 60
    allowed_senders: list[str] = field(default_factory=list)
    restrict_outbound: bool = True
    attachment_dir: str = "/tmp/attachments"
    state_file: Path = Path("./context/email_state.json")
    spam_score_threshold: float = 5.0
    # Outbound send-failure recovery. A failed reply is recorded in the
    # pending-reply ledger and re-sent by the poll-tick drain loop up to
    # send_max_retries total attempts (exponential backoff seeded by
    # send_retry_backoff_sec) before being dead-lettered.
    send_max_retries: int = 5
    send_retry_backoff_sec: float = 30.0
    # Consecutive send/poll failures before an ERROR-level escalation fires.
    failure_alert_threshold: int = 5
