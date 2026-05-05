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
    skill_dirs: list[Path] = field(
        default_factory=lambda: [Path("./skills"), Path("./context/skills")]
    )
    # Repetition detector — nudge/block the agent when it loops on near-identical
    # tool calls. See src/agent/repetition.py for semantics.
    repetition_nudge_threshold: int = 3
    repetition_block_threshold: int = 10
    repetition_similar_window: int = 5
    repetition_similar_jaccard: float = 0.5


@dataclass
class EmailChannelConfig:
    enabled: bool = False
    service_account_file: str = ""
    delegated_user: str = ""
    poll_interval_sec: int = 60
    allowed_senders: list[str] = field(default_factory=list)
    processed_label: str = "agent/processed"
    attachment_dir: str = "/tmp/attachments"
