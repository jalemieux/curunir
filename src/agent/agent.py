# src/agent/agent.py
import asyncio
import json
from datetime import datetime

from src.agent.system_prompt import build_static_prompt
from src.config import AgentConfig
from src.llm import call_llm
from src.tools.dispatcher import execute_tool_call, execute_tool_call_async, is_async_executor
from src.tools.schemas import get_tool_schemas


class Agent:
    def __init__(self, config: AgentConfig, exclude_tools: set[str] | None = None):
        self.config = config
        self.sessions: dict[str, list[dict]] = {}
        self.static_prompt = build_static_prompt(config)
        self.exclude_tools = {t.lower() for t in exclude_tools} if exclude_tools else set()

    def _get_tool_schemas(self) -> list[dict]:
        schemas = get_tool_schemas()
        if self.exclude_tools:
            schemas = [s for s in schemas if s["function"]["name"].lower() not in self.exclude_tools]
        return schemas

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

                    if name.lower() in self.exclude_tools:
                        history.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": f"Tool '{name}' is not available in this context.",
                        })
                        continue

                    if on_tool_call:
                        await on_tool_call(name, args_str)

                    if is_async_executor(name):
                        result = await execute_tool_call_async(
                            name,
                            json.loads(args_str),
                            self.config,
                        )
                    else:
                        result = await asyncio.to_thread(
                            execute_tool_call,
                            name,
                            json.loads(args_str),
                            self.config,
                        )
                    history.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": result,
                    })

                messages = [{"role": "system", "content": system_prompt}] + history
                continue

            if response.text:
                history.append({"role": "assistant", "content": response.text})
                return response.text

            history.append({"role": "assistant", "content": ""})
            return "Error: LLM returned empty response."

        return "Iteration limit reached."
