# src/agent/system_prompt.py
from src.agent.agents_config import load_agents_config
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
    manifest = build_skill_manifest(config.skills_dir)
    parts = [identity]
    if manifest:
        parts.append(manifest)
    return "\n\n".join(parts)


def build_orchestrator_prompt(config: AgentConfig) -> str:
    """Build the orchestrator system prompt: identity.md + dynamic specialist table.

    The identity file defines the assistant's persona and general guidelines.
    The specialist table is generated from agents.yaml at startup so the
    orchestrator knows which agents are available for delegation.
    """
    if not config.identity_file.exists():
        raise FileNotFoundError(
            f"Identity file not found: {config.identity_file}. "
            "Curunir requires an identity file to start."
        )
    identity = config.identity_file.read_text()

    agents = load_agents_config(config.agents_file)
    if not agents:
        return identity

    rows = [f"| {name} | {defn.description} |" for name, defn in agents.items()]
    specialists = (
        "## Specialists\n\n"
        "For tasks requiring tools, delegate to a specialist using the `delegate` tool. "
        "Include all context the specialist needs — they have no memory of this conversation. "
        "After a delegation, summarize the result for the user in 1-2 sentences.\n\n"
        "| Agent | Use for |\n"
        "|-------|--------|\n"
        + "\n".join(rows)
    )

    return identity.rstrip() + "\n\n" + specialists
