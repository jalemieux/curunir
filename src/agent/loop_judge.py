# src/agent/loop_judge.py
"""LLM-as-judge that decides whether the agent loop should continue or stop.

Invoked when ``Agent.handle()`` hits its iteration budget. The judge sees the
user's original request, a compact transcript of the recent iterations, and
the current token cost. It returns a structured decision so the loop can
either extend its budget or surface a meaningful summary to the user instead
of the bare ``"Iteration limit reached."`` string.
"""
import json
import logging
from dataclasses import dataclass
from typing import Literal

from src.llm import call_llm

log = logging.getLogger(__name__)

_TOOL_RESULT_MAX_CHARS = 500


@dataclass
class JudgeDecision:
    action: Literal["continue", "stop"]
    rationale: str
    summary: str


_JUDGE_PROMPT = """\
You are evaluating whether an AI agent's tool-calling loop should continue \
running or stop. The agent has hit its iteration budget. You decide whether \
to grant it more iterations or stop and surface a summary to the user.

## Signals to weigh

1. **Progress** — Is the agent making real progress, or repeating the same \
tool calls / arguments? Loops over the same failing command are wasted budget.
2. **Correctness** — Is the agent's work actually addressing the user's \
request, or has it drifted into something tangential?
3. **Cost** — How many tokens have been spent so far? Be more willing to \
stop when costs are already high.

Bias toward **stop** when uncertain. Granting more iterations to a stuck \
agent is worse than ending the session with a clear summary.

## Current state

- Iterations completed so far: {iterations_so_far}
- Extensions already granted this turn: {extension_number}
- Approximate tokens spent so far: {tokens_so_far}

## User's original request

{user_request}

## Recent iterations (most recent last)

{recent_transcript}

## Response format

Respond with ONLY valid JSON in this exact format inside a fenced code block:

```json
{{
  "decision": "continue" | "stop",
  "rationale": "one-sentence reason for the decision",
  "summary_if_stopping": "concise summary of what the agent attempted and \
any partial results, written for the end user (use even if you choose to \
continue — the agent's caller may surface it)"
}}
```
"""


def _content_to_text(content) -> str:
    """Flatten string / multimodal content to a plain text string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return str(content or "")


def _truncate(text: str, limit: int = _TOOL_RESULT_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated, {len(text) - limit} more chars]"


def summarize_recent_iterations(history: list[dict], last_n: int = 10) -> str:
    """Build a compact markdown transcript of the last N agent iterations.

    Each iteration = one assistant-with-tool-calls turn plus its tool
    responses. The user's original request (the first user message) is
    prepended verbatim. Tool results are truncated to keep the prompt cheap.
    """
    if not history:
        return "(empty history)"

    # Original user request: first user message.
    original_request = "(no user request found)"
    for msg in history:
        if msg.get("role") == "user":
            original_request = _content_to_text(msg.get("content")).strip()
            break

    # Walk backwards collecting assistant-with-tool-calls turns and the tool
    # responses that follow them. Stop once we have `last_n` iterations.
    iterations: list[tuple[dict, list[dict]]] = []
    i = len(history) - 1
    while i >= 0 and len(iterations) < last_n:
        msg = history[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            # Collect the tool responses that come after this turn.
            tool_msgs: list[dict] = []
            j = i + 1
            while j < len(history) and history[j].get("role") == "tool":
                tool_msgs.append(history[j])
                j += 1
            iterations.append((msg, tool_msgs))
        i -= 1

    iterations.reverse()  # chronological order

    lines: list[str] = [
        f"User's original request:\n{original_request}",
        "",
        "Recent iterations:",
    ]
    if not iterations:
        lines.append("(no tool-call iterations recorded)")
    for idx, (assistant_msg, tool_msgs) in enumerate(iterations, 1):
        lines.append(f"\n### Iteration {idx}")
        text = _content_to_text(assistant_msg.get("content"))
        if text.strip():
            lines.append(f"Assistant: {text.strip()}")
        for tc in assistant_msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            name = fn.get("name", "tool")
            args = fn.get("arguments", "")
            lines.append(f"- Tool call: `{name}` args={_truncate(str(args), 200)}")
        for t in tool_msgs:
            result = _content_to_text(t.get("content"))
            lines.append(f"  Result: {_truncate(result)}")
    return "\n".join(lines)


def _parse_json(text: str) -> dict | None:
    """Extract a JSON object from text, handling fenced code blocks."""
    if not text:
        return None
    cleaned = text.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                try:
                    return json.loads(part)
                except json.JSONDecodeError:
                    continue
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


async def judge_loop_progress(
    *,
    model: str,
    api_base: str | None,
    openrouter_provider: str | None,
    user_request: str,
    recent_transcript: str,
    iterations_so_far: int,
    extension_number: int,
    tokens_so_far: int,
) -> JudgeDecision:
    """Ask a cheap LLM whether to continue or stop the tool-loop.

    Returns a JudgeDecision. Fail-safe is ``stop`` — if the model returns
    unparseable output or the request fails, the loop ends with the best
    summary the judge could produce.
    """
    prompt = _JUDGE_PROMPT.format(
        iterations_so_far=iterations_so_far,
        extension_number=extension_number,
        tokens_so_far=tokens_so_far,
        user_request=user_request,
        recent_transcript=recent_transcript,
    )

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Make your decision now."},
    ]

    try:
        response = await call_llm(
            model, messages, tools=[],
            api_base=api_base,
            openrouter_provider=openrouter_provider,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("judge LLM call failed: %s — failing safe to stop", exc)
        return JudgeDecision(
            action="stop",
            rationale=f"judge call failed: {exc}",
            summary="The agent reached its iteration limit and the safety judge could not be reached.",
        )

    data = _parse_json(response.text or "")
    if data is None:
        log.warning("judge returned unparseable output: %r", (response.text or "")[:200])
        return JudgeDecision(
            action="stop",
            rationale="judge returned unparseable output",
            summary="The agent reached its iteration limit; the safety judge produced no usable decision so the loop was stopped.",
        )

    raw_decision = str(data.get("decision", "stop")).strip().lower()
    action: Literal["continue", "stop"] = "continue" if raw_decision == "continue" else "stop"
    rationale = str(data.get("rationale", "")).strip() or "(no rationale provided)"
    summary = str(data.get("summary_if_stopping", "")).strip() or "(no summary provided)"

    return JudgeDecision(action=action, rationale=rationale, summary=summary)
