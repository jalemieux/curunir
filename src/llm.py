# src/llm.py
from dataclasses import dataclass

import litellm


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[dict] | None


async def call_llm(model: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
    """Call LLM via LiteLLM, return normalized response."""
    kwargs = {
        "model": model,
        "messages": messages,
        "max_tokens": 16000,
    }
    if tools:
        kwargs["tools"] = tools

    response = await litellm.acompletion(**kwargs)
    choice = response.choices[0].message

    text = choice.content if choice.content else None

    tool_calls = None
    if choice.tool_calls:
        tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in choice.tool_calls
        ]

    return LLMResponse(text=text, tool_calls=tool_calls)
