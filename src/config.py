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
    # context_dir is the single root from which every per-persona state
    # artifact derives (memory/, conversations/, the SQLite stores,
    # identity.md, .ws-token, workspace/). Multi-tenant hosting forks all
    # per-persona state simply by handing each AgentRuntime its own
    # context_dir = context/<persona>. The fields below default to ``None``
    # and are derived from context_dir in __post_init__; pass them
    # explicitly only to override an individual artifact's location.
    context_dir: Path = Path("./context")
    identity_file: Path | None = None
    usage_db: Path | None = None
    schedules_db: Path | None = None
    portfolio_db: Path | None = None
    # FS-tool sandbox root for this persona (read/write/edit/glob/grep are
    # confined here when ``fs_jail`` is set). Defaults to context_dir/workspace
    # so a persona's filesystem tools can never reach a sibling persona's
    # context/<persona>/ tree. ``fs_jail`` opts the hardened realpath
    # containment guard in; multi-tenant runtimes set it per persona, while
    # the historical single-tenant default leaves it off.
    workdir: Path | None = None
    fs_jail: bool = False
    skill_dirs: list[Path] = field(
        default_factory=lambda: [Path("./skills"), Path("./context/skills")]
    )
    persona: str = "default"
    # Absolute repo-root anchor, derived once from this file's location
    # (src/config.py → parents[1] == repo root). The bash tool pins its
    # subprocess cwd here so commands run from the repo root regardless of
    # where the host process was launched — every config path is relative
    # to it (./context, ./skills, ...).
    repo_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[1])
    skill_allowlist: list[str] | None = None
    attachment_dir: str = "/tmp/attachments"
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"
    vision_model: str | None = None
    main_model_supports_vision: bool = False

    def __post_init__(self) -> None:
        self.context_dir = Path(self.context_dir)
        if self.identity_file is None:
            self.identity_file = self.context_dir / "identity.md"
        if self.usage_db is None:
            self.usage_db = self.context_dir / "usage.db"
        if self.schedules_db is None:
            self.schedules_db = self.context_dir / "schedules.db"
        if self.portfolio_db is None:
            self.portfolio_db = self.context_dir / "memory" / "portfolio.db"
        if self.workdir is None:
            self.workdir = self.context_dir / "workspace"


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
