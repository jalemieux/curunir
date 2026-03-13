# src/config.py
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AgentConfig:
    model: str = "anthropic/claude-sonnet-4-20250514"
    max_iterations: int = 15
    identity_file: Path = Path("./context/identity.md")
    skills_dir: Path = Path("./skills")
