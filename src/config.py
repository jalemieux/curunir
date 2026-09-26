# src/config.py
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentConfig:
    """Per-agent settings.

    Path layout (docs/superpowers/specs/2026-09-26-agents-and-containers-design.md):
    ``context_dir`` is the agent's private area (identity, memory,
    conversations, schedules, user skills) and ``shared_dir`` is the
    container's shared area (workspace deliverables, uploads, usage db,
    pairing token, email state). A bare ``AgentConfig()`` is the legacy
    single-agent layout where both are ``./context`` and every path field
    below keeps its historical literal default. ``for_agent`` derives the
    same fields from a chosen ``context_dir`` / ``shared_dir`` pair.
    """
    model: str = "anthropic/claude-sonnet-4-20250514"
    api_base: str | None = None
    openrouter_provider: str | None = None
    max_iterations: int = 200
    max_history_chars: int = 250_000
    max_tool_result_chars: int = 100_000
    # No-`limit` reads of files larger than this are gated: the read tool
    # returns the document card (if one exists) or a structural preview
    # instead of the full body (docs/document-ingestion.md). 0 disables.
    read_gate_bytes: int = 50_000
    identity_file: Path = Path("./context/identity.md")
    context_dir: Path = Path("./context")
    usage_db: Path = Path("./context/usage.db")
    schedules_db: Path = Path("./context/schedules.db")
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
    portfolio_db: Path = Path("./context/memory/portfolio.db")
    crm_db: Path = Path("./context/memory/crm.db")
    # Container-level fields. ``shared_dir`` defaults to ``context_dir`` so a
    # bare config is the legacy layout; ``agent_name`` / ``is_default`` name
    # this agent inside its container (the single legacy agent is the default).
    shared_dir: Path | None = None
    agent_name: str = "default"
    is_default: bool = True

    def __post_init__(self) -> None:
        if self.shared_dir is None:
            self.shared_dir = self.context_dir

    @property
    def path_vars(self) -> dict[str, str]:
        """Values for the ``{{context}}`` / ``{{shared}}`` placeholders.

        Rendered into skill and persona markdown by ``src.skills.render_paths``
        and exported to skill scripts by the bash tool as
        ``CURUNIR_CONTEXT_DIR`` / ``CURUNIR_SHARED_DIR``. For the legacy
        layout both are ``context``, so rendered text is byte-identical to
        the literals the markdown used to carry.
        """
        return {"context": str(self.context_dir), "shared": str(self.shared_dir)}

    @classmethod
    def for_agent(
        cls,
        name: str,
        context_dir: Path | str,
        shared_dir: Path | str | None = None,
        *,
        is_default: bool = True,
        **overrides,
    ) -> "AgentConfig":
        """Build a config whose per-agent paths derive from ``context_dir``.

        Derived from ``context_dir``: ``identity_file``, ``schedules_db``,
        ``portfolio_db``, ``crm_db`` and the user skills dir
        (``skill_dirs[1]``). Derived from ``shared_dir`` (default:
        ``context_dir``): ``usage_db``. ``overrides`` are applied last, so an
        explicit path or model setting still wins. ``for_agent("default",
        "./context")`` equals a bare ``AgentConfig()`` field for field.
        """
        context_dir = Path(context_dir)
        shared_dir = Path(shared_dir) if shared_dir is not None else context_dir
        derived = dict(
            agent_name=name,
            is_default=is_default,
            context_dir=context_dir,
            shared_dir=shared_dir,
            identity_file=context_dir / "identity.md",
            schedules_db=context_dir / "schedules.db",
            portfolio_db=context_dir / "memory" / "portfolio.db",
            crm_db=context_dir / "memory" / "crm.db",
            skill_dirs=[Path("./skills"), context_dir / "skills"],
            usage_db=shared_dir / "usage.db",
        )
        derived.update(overrides)
        return cls(**derived)


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
