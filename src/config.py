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
class EmailChannelConfig:
    enabled: bool = False
    api_key: str = ""
    inbox_id: str = ""
    api_base: str = "https://api.deadsimple.email"
    poll_interval_sec: int = 60
    allowed_senders: list[str] = field(default_factory=list)
    restrict_outbound: bool = True
    attachment_dir: str = "/tmp/attachments"
    state_file: Path = Path("./context/email_state.json")
    spam_score_threshold: float = 5.0
