# src/agent/system_prompt.py
from pathlib import Path

from src.config import AgentConfig
from src.skills import build_skill_manifest


def build_static_prompt(config: AgentConfig) -> str:
    """Build the static portion of the system prompt (identity + behavior + skill manifest).

    Identity (persona) and behavior (operating defaults) live in separate
    files so the `/identity` skill can edit persona without touching
    behavior. Both are concatenated into the system prompt at boot.

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
    parts = [identity]

    # Behavior layer (operating defaults, PR #282): optional, appended if present.
    if config.behavior_file.exists():
        parts.append(config.behavior_file.read_text())

    # Persona expertise layer: domain .md files bootstrapped into
    # context/persona/. Sorted by filename so authors can order with numeric
    # prefixes (10-domain.md, 20-guardrails.md). Absent dir is skipped.
    if config.persona_prompt_dir.is_dir():
        for md in sorted(config.persona_prompt_dir.glob("*.md")):
            parts.append(md.read_text())

    manifest = build_skill_manifest(
        config.skill_dirs,
        set(config.skill_allowlist) if config.skill_allowlist else None,
    )
    if manifest:
        parts.append(manifest)
    return "\n\n".join(parts)


def build_memory_block(context_dir: Path) -> str:
    """Coalesce memory/README.md and memory/profile.md for the system prompt.

    Appended once per session so the routing map and owner profile are always
    in context. Both files are optional — missing files are silently skipped.
    Returns an empty string when neither file exists.
    """
    memory_dir = context_dir / "memory"
    parts: list[str] = []

    readme = memory_dir / "README.md"
    if readme.exists():
        parts.append(readme.read_text())

    profile = memory_dir / "profile.md"
    if profile.exists():
        parts.append(profile.read_text())

    return "\n\n".join(parts)
