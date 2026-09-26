# src/tools/ask_agent.py
"""``ask_agent``: two-way collaboration inside a container.

Shaped like ``delegate``: the sibling's own config (persona, skills, context)
answers in a transient ``Agent`` with a restricted tool list, in a session
that is never persisted (``conversation_store.save`` only runs in
``agent_worker``, and extraction is disk-driven, so the transcript leaves no
trace). The answer returns to the asker as the tool result. See
``docs/superpowers/specs/2026-09-26-agents-and-containers-design.md``.
"""
import asyncio
import logging
from uuid import uuid4

from src.agent.agent import Agent
from src.config import AgentConfig
from src.llm import classify_provider_error
from src.tools.delegate import _SUB_AGENT_TOOLS

logger = logging.getLogger(__name__)

# The sibling answers with the same tool set as a delegate sub-agent: no
# delegate, no ask_agent, so a question cannot fan out or recurse.
_ASK_TOOLS = list(_SUB_AGENT_TOOLS)

# Hard limit for one answer, in seconds.
_TIMEOUT = 600


def _frame(asker: str, question: str) -> str:
    return f"[Question from sibling agent '{asker}']\n\n{question}"


async def exec_ask_agent(args: dict, config: AgentConfig, agent=None, on_tool_call=None) -> str:
    """Run the sibling named in ``args['agent']`` on ``args['question']``."""
    question = (args.get("question") or "").strip()
    if not question:
        return "Error: 'question' is required"
    name = (args.get("agent") or "").strip()
    if not name:
        return "Error: 'agent' is required"

    container = getattr(agent, "container", None)
    if container is None or not container.multi_agent:
        return "Error: ask_agent is only available inside a multi-agent container"
    asker = config.agent_name
    sibling = container.agents.get(name)
    siblings = [n for n in container.manifest.agent_names if n != asker]
    if sibling is None or name == asker:
        return f"Error: unknown sibling agent {name!r}. Siblings: {', '.join(siblings)}"

    sub = Agent(sibling.config, tools=_ASK_TOOLS, usage_store=sibling.usage_store)
    session_id = f"ask:{asker}:{uuid4().hex}"
    logger.info("ask_agent %s -> %s (%s): %.80s", asker, name, session_id[-8:], question)
    try:
        result = await asyncio.wait_for(
            sub.handle(_frame(asker, question), session_id, on_tool_call=on_tool_call),
            timeout=_TIMEOUT,
        )
        logger.info("ask_agent %s -> %s completed", asker, name)
        return result
    except asyncio.TimeoutError:
        logger.warning("ask_agent %s -> %s timed out after %ds", asker, name, _TIMEOUT)
        return (
            f"Sibling agent '{name}' hit the {_TIMEOUT}s time limit and was "
            "stopped; no partial answer is available. Ask a narrower question "
            "or answer without it."
        )
    except Exception as e:
        logger.error("ask_agent %s -> %s failed: %s", asker, name, e)
        classified = classify_provider_error(e)
        if classified is not None:
            _, user_message = classified
            return f"Sibling agent '{name}' could not run: {user_message}"
        return f"Sibling agent '{name}' error: {e}"
