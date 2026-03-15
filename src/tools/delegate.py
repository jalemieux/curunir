# src/tools/delegate.py
import asyncio
import base64
import logging
import mimetypes
from uuid import uuid4

from src.agent.agent import Agent
from src.config import AgentConfig

logger = logging.getLogger(__name__)

# Tools available to sub-agents (everything except delegate — no recursive spawning)
_SUB_AGENT_TOOLS = ["glob", "grep", "read", "edit", "write", "bash", "load_skill"]

# Sub-agent timeout in seconds
_TIMEOUT = 300


async def exec_delegate(args: dict, config: AgentConfig) -> str:
    """Spawn a sub-agent with a clean context window and return its response."""
    task = args.get("task", "")
    if not task:
        return "Error: 'task' is required"

    image_paths = args.get("image_paths", [])
    # Guard against LLM sending a string instead of a list
    if isinstance(image_paths, str):
        image_paths = [image_paths]

    # Build the sub-agent's input: text, or multimodal blocks if images
    if image_paths:
        content = _build_multimodal_content(task, image_paths)
    else:
        content = task

    sub_agent = Agent(config, tools=_SUB_AGENT_TOOLS)
    session_id = str(uuid4())

    logger.info("Spawning sub-agent %s: %.80s", session_id[:8], task)
    try:
        result = await asyncio.wait_for(
            sub_agent.handle(content, session_id),
            timeout=_TIMEOUT,
        )
        logger.info("Sub-agent %s completed", session_id[:8])
        return result
    except asyncio.TimeoutError:
        logger.warning("Sub-agent %s timed out after %ds", session_id[:8], _TIMEOUT)
        return f"Sub-agent timed out after {_TIMEOUT}s"
    except Exception as e:
        logger.error("Sub-agent %s failed: %s", session_id[:8], e)
        return f"Sub-agent error: {e}"


def _build_multimodal_content(task: str, image_paths: list[str]) -> list[dict]:
    """Build multimodal content blocks with base64-encoded images."""
    blocks: list[dict] = [{"type": "text", "text": task}]

    for path in image_paths:
        try:
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            mime = mimetypes.guess_type(path)[0] or "image/png"
            blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{data}"},
            })
        except Exception as e:
            logger.warning("Failed to read image %s: %s", path, e)
            blocks.append({"type": "text", "text": f"(Could not read image: {path})"})

    return blocks
