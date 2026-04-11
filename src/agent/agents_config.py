"""Load sub-agent definitions from agents.yaml."""

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubAgentDef:
    name: str
    description: str
    tools: list[str]
    system_prompt: str
    max_iterations: int = 10


def load_agents_config(path: Path) -> dict[str, SubAgentDef]:
    """Load agents.yaml and return a dict of agent name -> SubAgentDef.

    Returns an empty dict if the file doesn't exist.
    """
    if not path.is_file():
        logger.debug("No agents config at %s", path)
        return {}

    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        logger.warning("agents.yaml is not a mapping, ignoring")
        return {}

    agents: dict[str, SubAgentDef] = {}
    for name, defn in raw.items():
        if not isinstance(defn, dict):
            continue
        agents[name] = SubAgentDef(
            name=name,
            description=defn.get("description", ""),
            tools=defn.get("tools", []),
            system_prompt=defn.get("system_prompt", ""),
            max_iterations=defn.get("max_iterations", 10),
        )

    logger.info("Loaded %d sub-agent definitions: %s", len(agents), ", ".join(agents))
    return agents
