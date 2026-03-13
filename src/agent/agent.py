# src/agent/agent.py
import asyncio
import json
from datetime import datetime

from src.agent.system_prompt import build_static_prompt
from src.config import AgentConfig
from src.llm import call_llm
from src.tools.dispatcher import execute_tool_call
from src.tools.schemas import get_tool_schemas


class Agent:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.sessions: dict[str, list[dict]] = {}
        self.static_prompt = build_static_prompt(config)

    async def handle(self, message: str, session_id: str) -> str:
        """Process a message and return the agent's response."""
        history = self.sessions.setdefault(session_id, [])
        history.append({"role": "user", "content": message})

        system_prompt = self.static_prompt + f"\n\nCurrent time: {datetime.now().isoformat()}"
        messages = [{"role": "system", "content": system_prompt}] + history

        for _ in range(self.config.max_iterations):
            response = await call_llm(self.config.model, messages, get_tool_schemas())

            if response.tool_calls:
                assistant_msg: dict = {"role": "assistant", "tool_calls": response.tool_calls}
                if response.text:
                    assistant_msg["content"] = response.text
                history.append(assistant_msg)

                for tool_call in response.tool_calls:
                    result = await asyncio.to_thread(
                        execute_tool_call,
                        tool_call["function"]["name"],
                        json.loads(tool_call["function"]["arguments"]),
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
