from src.config import AgentConfig
from src.skills import load_skill


def exec_load_skill(args: dict, config: AgentConfig) -> str:
    """Load a skill's instructions by name."""
    return load_skill(args["name"], config.skills_dir)
