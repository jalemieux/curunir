# src/agent/system_prompt.py
import logging
from pathlib import Path

from src.config import AgentConfig
from src.persona import prompts_dir
from src.skills import build_skill_manifest

logger = logging.getLogger(__name__)


def build_static_prompt(config: AgentConfig) -> str:
    """Build the static portion of the system prompt.

    Order:
        context/identity.md   (user-customized assistant persona)
        personas/<active>/prompts/*.md  (sorted; framework + specialty)
        skill manifest        (filtered by persona allowlist when set)

    The persona bundle owns everything framework-level — behavior defaults,
    domain expertise, guardrails. `context/` is the user-edited layer
    (identity only). Persona files are read directly from the bundle; they
    do not bootstrap into `context/`.

    Identity is the personality layer only — one of several optional layers
    (behavior, persona expertise, skill manifest). It is no longer load-bearing
    for correctness, so a missing identity.md warns rather than failing: the
    agent boots with a default (faceless) personality. A missing file usually
    means onboarding hasn't run or context/ wasn't mounted, hence the warning.

    This prefix carries no timestamp, so it is byte-stable across every session
    and the whole process lifetime — required for auto-cache providers (OpenAI /
    DeepSeek / xAI / GLM via OpenRouter) to hit the cache. Time enters as two
    correctly-scoped signals elsewhere: a stable per-session "Conversation
    started at" line (added in Agent._get_session_prompt, sourced from the
    conversation's persisted created_at) and a live per-turn "Current date/time"
    note injected *outside* this cached prefix as a trailing message in
    Agent.handle(). The split keeps the cacheable prefix stable while still
    giving the model a fresh clock each turn.
    """
    parts = []
    if config.identity_file.exists():
        parts.append(config.identity_file.read_text())
    else:
        logger.warning(
            "Identity file not found: %s. Booting with a default personality "
            "(no identity layer). Run onboarding to generate one, or ensure "
            "context/ is mounted/synced.",
            config.identity_file,
        )

    prompts = prompts_dir(config.persona)
    if prompts.is_dir():
        for md in sorted(prompts.glob("*.md")):
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
