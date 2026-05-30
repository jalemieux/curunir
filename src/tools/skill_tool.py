from src.config import AgentConfig
from src.skills import load_skill


def exec_load_skill(args: dict, config: AgentConfig) -> str:
    """Load a skill's instructions by name, scoped to the persona allowlist."""
    allowlist = set(config.skill_allowlist) if config.skill_allowlist else None
    return load_skill(args["name"], config.skill_dirs, allowlist)
