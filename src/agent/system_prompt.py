# src/agent/system_prompt.py
import logging
from pathlib import Path

from src.config import AgentConfig
from src.persona import prompts_dir
from src.skills import build_skill_manifest, render_paths

logger = logging.getLogger(__name__)


def build_static_prompt(config: AgentConfig, announce: bool = True) -> str:
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

    This prefix carries no timestamp, so it is a pure function of the files
    it reads: rebuilt from unchanged files it is byte-identical, which is all
    auto-cache providers (OpenAI / DeepSeek / xAI / GLM via OpenRouter) need to
    hit the cache. Agent._get_session_prompt therefore builds it at the start
    of each conversation and freezes it for that conversation, so an edited
    identity.md or a newly authored skill shows up in the next conversation
    without a restart, and the cache only misses when a file actually changed.
    Time enters as two correctly-scoped signals elsewhere: a stable per-session
    "Conversation started at" line (added in Agent._get_session_prompt, sourced
    from the conversation's persisted created_at) and a live per-turn "Current
    date/time" note injected *outside* this cached prefix as a trailing message
    in Agent.handle(). The split keeps the cacheable prefix stable while still
    giving the model a fresh clock each turn.

    ``announce=False`` is the per-conversation rebuild: it skips the
    missing-identity warning and the manifest listing, which the boot-time
    build already logged once (scheduled jobs and ask_agent open many
    sessions).
    """
    parts = []
    if config.identity_file.exists():
        parts.append(config.identity_file.read_text())
    elif announce:
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
        announce=announce,
    )
    if manifest:
        parts.append(manifest)
    # Render {{context}} / {{shared}} once over the assembled prefix so the
    # identity, persona prompts and skill descriptions in the manifest all
    # name this agent's real directories.
    return render_paths("\n\n".join(parts), config.path_vars)


def build_memory_block(config: AgentConfig) -> str:
    """Coalesce memory/README.md, the shared profile and the agent's own
    user notes for the system prompt.

    Appended once per session so the routing map and owner profile are always
    in context. The README is the agent's own (``<context>/memory/README.md``);
    the profile is the container's single ``<shared>/profile.md``, so every
    agent sees the same one; ``<context>/memory/user.md`` is what the user
    told this agent alone and follows the profile. All are optional — missing
    files are silently skipped. Returns an empty string when none exists.
    """
    parts: list[str] = []

    readme = config.context_dir / "memory" / "README.md"
    if readme.exists():
        parts.append(readme.read_text())

    profile = config.profile_file
    if profile.exists():
        parts.append(profile.read_text())

    user_notes = config.context_dir / "memory" / "user.md"
    if user_notes.exists():
        parts.append(user_notes.read_text())

    return render_paths("\n\n".join(parts), config.path_vars)
