# src/tools/delegate.py
"""Delegate tool — spawn a named sub-agent with restricted tools and context."""

import asyncio
import logging
from dataclasses import replace
from uuid import uuid4

from src.agent.agent import Agent
from src.agent.agents_config import load_agents_config
from src.config import AgentConfig

logger = logging.getLogger(__name__)

_TIMEOUT = 300
_MAX_RESULT_CHARS = 2048  # ~500 tokens safety net


async def exec_delegate(args: dict, config: AgentConfig, on_tool_call=None) -> str:
    """Spawn a named sub-agent and return its response."""
    agent_name = args.get("agent", "")
    task = args.get("task", "")
    if not agent_name:
        return "Error: 'agent' is required"
    if not task:
        return "Error: 'task' is required"

    agents = load_agents_config(config.agents_file)
    if not agents:
        return "Error: no agents configured (agents.yaml not found)"

    defn = agents.get(agent_name)
    if not defn:
        available = ", ".join(sorted(agents.keys()))
        return f"Error: unknown agent '{agent_name}'. Available: {available}"

    # Build a sub-agent config with the agent's iteration cap
    sub_config = replace(config, max_iterations=defn.max_iterations)

    sub_agent = Agent(
        sub_config,
        tools=defn.tools,
        system_prompt_override=defn.system_prompt,
    )
    session_id = str(uuid4())

    logger.info("Delegating to [%s] agent %s: %.80s", session_id[:8], agent_name, task)
    try:
        result = await asyncio.wait_for(
            sub_agent.handle(task, session_id, on_tool_call=on_tool_call),
            timeout=_TIMEOUT,
        )
        logger.info("Agent [%s] %s completed", session_id[:8], agent_name)
    except asyncio.TimeoutError:
        logger.warning("Agent [%s] %s timed out after %ds", session_id[:8], agent_name, _TIMEOUT)
        return f"Sub-agent '{agent_name}' timed out after {_TIMEOUT}s"
    except Exception as e:
        logger.error("Agent [%s] %s failed: %s", session_id[:8], agent_name, e)
        return f"Sub-agent '{agent_name}' error: {e}"

    # Truncate long results as a safety net
    if len(result) > _MAX_RESULT_CHARS:
        result = result[:_MAX_RESULT_CHARS] + "... (truncated)"

    return result
