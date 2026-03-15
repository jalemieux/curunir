# src/agent/agent.py
import json
from datetime import datetime

from src.agent.system_prompt import build_static_prompt
from src.config import AgentConfig
from src.llm import call_llm
from src.tools.dispatcher import execute_tool_call
from src.tools.schemas import get_tool_schemas


_MAX_HISTORY_CHARS = 600_000  # ~150k tokens, leaves room for system prompt + response


def _estimate_chars(messages: list[dict]) -> int:
    """Rough character count across all message contents."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for block in content:
                total += len(str(block))
    return total


def _trim_history(history: list[dict], max_chars: int = _MAX_HISTORY_CHARS) -> None:
    """Remove oldest messages in coherent groups until under the char limit.

    Groups: user+assistant pairs, or assistant(tool_calls)+tool+...+tool sequences.
    Always removes from the front so the most recent context is preserved.
    After trimming, the first message should be role=user.
    """
    while len(history) > 2 and _estimate_chars(history) > max_chars:
        # Remove messages from the front until we hit the next "user" message
        history.pop(0)
        while history and history[0]["role"] != "user":
            history.pop(0)


class Agent:
    def __init__(self, config: AgentConfig, tools: list[str] | None = None):
        self.config = config
        self.sessions: dict[str, list[dict]] = {}
        self.static_prompt = build_static_prompt(config)
        self.tools = tools  # None = all tools

    def _get_tool_schemas(self) -> list[dict]:
        return get_tool_schemas(self.tools)

    async def handle(self, message: str | list, session_id: str, on_tool_call=None) -> str:
        """Process a message and return the agent's response.

        Args:
            message: User input text, or a list of content blocks for multimodal input.
            session_id: Session identifier for conversation history.
            on_tool_call: Optional async callback called with (name, args_str)
                          for each tool call, enabling real-time UI updates.
        """
        history = self.sessions.setdefault(session_id, [])
        history.append({"role": "user", "content": message})

        system_prompt = self.static_prompt + f"\n\nCurrent time: {datetime.now().isoformat()}"
        _trim_history(history)
        messages = [{"role": "system", "content": system_prompt}] + history

        for _ in range(self.config.max_iterations):
            response = await call_llm(self.config.model, messages, self._get_tool_schemas())

            if response.tool_calls:
                assistant_msg: dict = {"role": "assistant", "tool_calls": response.tool_calls}
                if response.text:
                    assistant_msg["content"] = response.text
                history.append(assistant_msg)

                for tool_call in response.tool_calls:
                    name = tool_call["function"]["name"]
                    args_str = tool_call["function"]["arguments"]

                    if on_tool_call:
                        await on_tool_call(name, args_str)

                    result = await execute_tool_call(
                        name,
                        json.loads(args_str),
                        self.config,
                    )
                    history.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": result,
                    })

                _trim_history(history)
                messages = [{"role": "system", "content": system_prompt}] + history
                continue

            if response.text:
                history.append({"role": "assistant", "content": response.text})
                return response.text

            history.append({"role": "assistant", "content": ""})
            return "Error: LLM returned empty response."

        return "Iteration limit reached."
