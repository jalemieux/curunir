# src/tools/run_skill.py
"""run_skill tool — spawn a sub-agent configured by a skill definition."""

import asyncio
import logging
from dataclasses import replace
from uuid import uuid4

from src.agent.agent import Agent
from src.config import AgentConfig
from src.skills import load_skill_def

logger = logging.getLogger(__name__)

_TIMEOUT = 300
_CHARS_PER_TOKEN = 4


async def exec_run_skill(args: dict, config: AgentConfig, on_tool_call=None) -> str:
    """Load a skill, spawn a fresh sub-agent, return its response."""
    skill_name = args.get("skill", "")
    task = args.get("task", "")
    intent = args.get("intent", "")
    if not skill_name:
        return "Error: 'skill' is required"
    if not task:
        return "Error: 'task' is required"
    if not intent:
        return (
            "Error: 'intent' is required — state what the caller needs back "
            "(e.g. 'one-paragraph summary', 'confirm the fix applied', "
            "'the list of PR numbers')."
        )

    defn = load_skill_def(skill_name, config.skills_dir)
    if defn is None:
        return f"Error: unknown skill '{skill_name}' (or missing required frontmatter)"

    sub_config = replace(config, max_iterations=defn.max_iterations)
    sub_agent = Agent(
        sub_config,
        tools=defn.tools,
        system_prompt_override=defn.body,
    )
    session_id = str(uuid4())
    sub_agent_message = f"Task: {task}\nIntent: {intent}"

    logger.info(
        "Running skill [%s] %s: task=%.60s intent=%.60s",
        session_id[:8], skill_name, task, intent,
    )
    try:
        result = await asyncio.wait_for(
            sub_agent.handle(sub_agent_message, session_id, on_tool_call=on_tool_call),
            timeout=_TIMEOUT,
        )
        logger.info("Skill [%s] %s completed", session_id[:8], skill_name)
    except asyncio.TimeoutError:
        logger.warning("Skill [%s] %s timed out after %ds", session_id[:8], skill_name, _TIMEOUT)
        return f"Skill '{skill_name}' timed out after {_TIMEOUT}s"
    except Exception as e:
        logger.error("Skill [%s] %s failed: %s", session_id[:8], skill_name, e)
        return f"Skill '{skill_name}' error: {e}"

    max_chars = defn.max_output_tokens * _CHARS_PER_TOKEN
    if len(result) > max_chars:
        dropped_tokens = (len(result) - max_chars) // _CHARS_PER_TOKEN
        result = result[:max_chars] + f"\n... [truncated: ~{dropped_tokens} tokens omitted]"

    return result
