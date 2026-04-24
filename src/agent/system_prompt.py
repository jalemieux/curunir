# src/agent/system_prompt.py
from src.config import AgentConfig
from src.skills import build_skill_manifest


def build_static_prompt(config: AgentConfig) -> str:
    """Build the static portion of the system prompt (identity + skill manifest).

    Timestamp is appended per-call in Agent.handle().
    """
    if not config.identity_file.exists():
        raise FileNotFoundError(
            f"Identity file not found: {config.identity_file}. "
            "Curunir requires an identity file to start."
        )
    identity = config.identity_file.read_text()
    manifest = build_skill_manifest(config.skill_dirs)
    parts = [identity]
    if manifest:
        parts.append(manifest)
    return "\n\n".join(parts)
