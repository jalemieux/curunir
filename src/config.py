# src/config.py
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentConfig:
    model: str = "anthropic/claude-sonnet-4-20250514"
    api_base: str | None = None
    openrouter_provider: str | None = None
    max_iterations: int = 75
    max_history_chars: int = 250_000
    identity_file: Path = Path("./context/identity.md")
    context_dir: Path = Path("./context")
    usage_db: Path = Path("./context/usage.db")
    skill_dirs: list[Path] = field(
        default_factory=lambda: [Path("./skills"), Path("./context/skills")]
    )
    attachment_dir: str = "/tmp/attachments"
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"


@dataclass
class EmailChannelConfig:
    enabled: bool = False
    service_account_file: str = ""
    delegated_user: str = ""
    poll_interval_sec: int = 60
    allowed_senders: list[str] = field(default_factory=list)
    processed_label: str = "agent/processed"
    attachment_dir: str = "/tmp/attachments"
