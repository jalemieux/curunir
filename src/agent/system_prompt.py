# src/agent/system_prompt.py
from src.config import AgentConfig
from src.skills import build_skill_manifest


_DELEGATION_PRINCIPLE = (
    "## Delegating work\n\n"
    "You have direct tools for quick, unstructured tasks, and skills for "
    "procedural or heavy work. Prefer `run_skill` when a task matches a "
    "skill's description, is likely to produce large output, or would require "
    "more than ~3 tool calls. Use direct tools only for quick one-offs.\n\n"
    "Every `run_skill` call spawns a fresh sub-agent with no memory of this "
    "conversation, so pass both `task` (the action) and `intent` (what you "
    "need back — the user's goal, not a restatement of the task)."
)


def build_static_prompt(config: AgentConfig) -> str:
    """Build the static system prompt for big-model mode (identity + manifest)."""
    if not config.identity_file.exists():
        raise FileNotFoundError(
            f"Identity file not found: {config.identity_file}. "
            "Curunir requires an identity file to start."
        )
    identity = config.identity_file.read_text()
    manifest = build_skill_manifest(config.skills_dir)
    parts = [identity]
    if manifest:
        parts.append(manifest)
    return "\n\n".join(parts)


def build_orchestrator_prompt(config: AgentConfig) -> str:
    """Build the small-model orchestrator prompt: identity + delegation principle + skill manifest."""
    if not config.identity_file.exists():
        raise FileNotFoundError(
            f"Identity file not found: {config.identity_file}. "
            "Curunir requires an identity file to start."
        )
    identity = config.identity_file.read_text()
    manifest = build_skill_manifest(config.skills_dir)

    parts = [identity.rstrip(), _DELEGATION_PRINCIPLE]
    if manifest:
        parts.append(manifest)
    return "\n\n".join(parts)
