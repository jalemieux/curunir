# src/agent/system_prompt.py
from pathlib import Path

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


def build_orchestrator_prompt(name: str, agents_file: Path) -> str:
    """Build a minimal orchestrator system prompt from agents.yaml.

    The orchestrator's job is to route tasks to specialists and
    answer simple questions directly. The prompt is kept small
    (~300 tokens) to fit within constrained context windows.
    """
    agents = load_agents_config(agents_file)

    # Build specialist table
    if agents:
        rows = []
        for agent_name, defn in agents.items():
            rows.append(f"| {agent_name} | {defn.description} |")
        table = "| Agent | Use for |\n|-------|--------|\n" + "\n".join(rows)
    else:
        table = "(No specialists configured)"

    return f"""You are {name}, a personal assistant running on local hardware.

You can answer simple questions directly. For tasks requiring tools, delegate to a specialist.

## Specialists
{table}

## Rules
- Delegate by calling the delegate tool with an agent name and a concise task description.
- Include all context the specialist needs in the task — they have no memory of this conversation.
- For multi-step tasks, delegate one step at a time and use each result to inform the next.
- After each delegation, summarize the result to the user in 1-2 sentences.
- If no tools are needed, respond directly."""
