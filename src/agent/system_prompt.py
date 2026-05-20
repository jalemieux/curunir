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
    """Return memory/README.md for inlining into the system prompt.

    Only the routing map is inlined. profile.md and the rest of memory/ are
    reached via tools — inlining profile.md previously caused the agent to
    treat its contents as the entire owner-knowledge surface and refuse to
    check sibling files (e.g. people/) when asked about anyone not listed
    inline. Returns an empty string when README.md is absent.
    """
    readme = context_dir / "memory" / "README.md"
    return readme.read_text() if readme.exists() else ""
