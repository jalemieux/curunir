# src/agent/system_prompt.py
from pathlib import Path

from src.config import AgentConfig
from src.skills import build_skill_manifest


def build_static_prompt(config: AgentConfig) -> str:
    """Build the static portion of the system prompt (identity + skill manifest).

    Agent.__init__ appends a single boot-time timestamp on top of this so the
    full system block is byte-stable across calls — required for auto-cache
    providers (OpenAI / DeepSeek / xAI / GLM via OpenRouter) to hit the cache.
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


def build_memory_block(context_dir: Path) -> str:
    """Coalesce memory/README.md and memory/profile.md into a delimited block.

    The block is intended to be appended to the system prompt at the start of
    a session so the routing map and owner profile are always in context. Both
    files are optional — missing files are silently skipped. Returns an empty
    string when neither file exists.
    """
    memory_dir = context_dir / "memory"
    parts: list[str] = []

    readme = memory_dir / "README.md"
    if readme.exists():
        parts.append(f"<memory_routing>\n{readme.read_text()}\n</memory_routing>")

    profile = memory_dir / "profile.md"
    if profile.exists():
        parts.append(f"<memory_profile>\n{profile.read_text()}\n</memory_profile>")

    return "\n\n".join(parts)
