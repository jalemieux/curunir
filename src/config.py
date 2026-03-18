# src/config.py
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentConfig:
    model: str = "anthropic/claude-sonnet-4-20250514"
    api_base: str | None = None
    openrouter_provider: str | None = None
    max_iterations: int = 75
    identity_file: Path = Path("./context/identity.md")
    context_dir: Path = Path("./context")
    skills_dir: Path = Path("./skills")


@dataclass
class EmailChannelConfig:
    enabled: bool = False
    account: str = ""
    poll_interval_sec: int = 60
    allowed_senders: list[str] = field(default_factory=list)
    processed_label: str = "agent/processed"
    attachment_dir: str = "/tmp/attachments"
