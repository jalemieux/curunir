# src/agent/agent.py
import asyncio
import json
import logging
import time

import litellm

from src.agent.system_prompt import build_static_prompt

logger = logging.getLogger(__name__)
from src.config import AgentConfig
from src.llm import call_llm
from src.skills import parse_frontmatter
from src.tools.dispatcher import execute_tool_call
from src.tools.schemas import get_tool_schemas


TRIM_THRESHOLD = 0.85  # proactive trim when last_prompt_tokens > THRESHOLD * n_ctx
TRIM_FRACTION = 0.5    # keep this fraction of messages when trimming

# Map tool names to their key argument(s) for log display
_TOOL_KEY_ARGS: dict[str, list[str]] = {
    "web_fetch": ["url"],
    "bash": ["command"],
    "write": ["file_path"],
    "read": ["file_path"],
    "edit": ["file_path"],
    "glob": ["pattern"],
    "grep": ["pattern"],
    "load_skill": ["name"],
    "attach": ["path"],
    "run_skill": ["skill"],
}

_MAX_ARG_LEN = 120


def _display_name(tool_name: str) -> str:
    """Convert snake_case tool name to PascalCase display name."""
    return "".join(part.capitalize() for part in tool_name.split("_"))


def _tool_detail_lines(name: str, args_str: str) -> list[str]:
    """Build tree-formatted detail lines for a tool call."""
    try:
        args = json.loads(args_str)
    except (json.JSONDecodeError, TypeError):
        return [f"├─ {_display_name(name)} (unparseable args)"]

    key_names = _TOOL_KEY_ARGS.get(name, list(args.keys())[:1])
    lines = []
    for key in key_names:
        val = args.get(key, "")
        val_str = str(val)
        if len(val_str) > _MAX_ARG_LEN:
            val_str = val_str[:_MAX_ARG_LEN] + "..."
        lines.append(f"{_display_name(name)} {val_str}")

    if not lines:
        return [f"╰─ {_display_name(name)}"]

    result = []
    for i, line in enumerate(lines):
        connector = "╰─" if i == len(lines) - 1 else "├─"
        result.append(f"{connector} {line}")
    return result


def _trim_history(history: list[dict], target_messages: int) -> None:
    """Drop oldest user-grouped sequences until len(history) <= target_messages.

    Groups: a user message plus everything up to (but not including) the next
    user message. Always preserves at least one user message so the API call
    has content. For single-user-message sessions (system tasks), drops
    assistant+tool groups after the user message instead.
    """
    if target_messages < 1:
        target_messages = 1

    user_count = sum(1 for m in history if m["role"] == "user")

    if user_count > 1:
        while user_count > 1 and len(history) > target_messages:
            if history[0]["role"] == "user":
                user_count -= 1
            history.pop(0)
            while history and history[0]["role"] != "user":
                history.pop(0)
    elif user_count == 1 and len(history) > 1:
        while len(history) > 1 and len(history) > target_messages:
            del history[1]
            while len(history) > 1 and history[1]["role"] == "tool":
                del history[1]


def _is_context_overflow(exc: Exception) -> bool:
    """Check if an exception is a context window / input length overflow."""
    if isinstance(exc, litellm.ContextWindowExceededError):
        return True
    msg = str(exc).lower()
    return "context limit" in msg or "prompt is too long" in msg or "exceed" in msg and "token" in msg


def _discover_skill_names(skills_dir) -> list[str]:
    """Return sorted skill directory names that contain a SKILL.md."""
    if not skills_dir.exists():
        return []
    return sorted(
        p.parent.name for p in skills_dir.glob("*/SKILL.md")
    )


def _parse_skill_tools(skill_content: str) -> list[str]:
    """Extract required tool names from a skill's frontmatter."""
    fm = parse_frontmatter(skill_content)
    tools = fm.get("tools")
    if not tools:
        return []
    if isinstance(tools, list):
        return [str(t).strip() for t in tools if str(t).strip()]
    return [t.strip() for t in str(tools).split(",") if t.strip()]


class Agent:
    def __init__(self, config: AgentConfig, tools: list[str] | None = None,
                 system_prompt_override: str | None = None):
        self.config = config
        self.sessions: dict[str, list[dict]] = {}
        if system_prompt_override:
            self.static_prompt = system_prompt_override
        else:
            self.static_prompt = build_static_prompt(config)
        self.tools = tools  # None = all tools
        self._session_tools: dict[str, set[str]] = {}  # extra tools loaded by skills

        # If this agent exposes run_skill, discover skill names for the schema enum
        self._skill_names: list[str] | None = None
        if tools and "run_skill" in tools:
            self._skill_names = _discover_skill_names(config.skills_dir)

    def _get_tool_schemas(self, session_id: str | None = None) -> list[dict]:
        import copy
        base = get_tool_schemas(self.tools)
        if session_id and session_id in self._session_tools:
            extra = get_tool_schemas(list(self._session_tools[session_id]))
            base = base + extra

        # Inject skill-name enum into run_skill schema
        if self._skill_names:
            base = copy.deepcopy(base)
            for schema in base:
                if schema["function"]["name"] == "run_skill":
                    schema["function"]["parameters"]["properties"]["skill"]["enum"] = self._skill_names
                    break

        return base

    async def handle(
        self, message: str | list, session_id: str,
        on_tool_call=None, attachments: list[dict] | None = None,
        system_task_prompt: str | None = None,
        metadata: dict | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> str:
        """Process a message and return the agent's response.

        Args:
            message: User input text, or a list of content blocks for multimodal input.
            session_id: Session identifier for conversation history.
            on_tool_call: Optional async callback called with (name, args_str)
                          for each tool call, enabling real-time UI updates.
            attachments: Optional list that will be populated with any files
                         the agent attaches during this request via the attach tool.
        """
        history = self.sessions.setdefault(session_id, [])

        if system_task_prompt:
            # System-initiated task: inject task as a user message so all LLM
            # providers accept the request (some reject system-only conversations).
            history.append({"role": "user", "content": f"## Scheduled Task\n{system_task_prompt}"})
        else:
            history.append({"role": "user", "content": message})
        system_prompt = self.static_prompt
        messages = [{"role": "system", "content": system_prompt}] + history

        sid = session_id[:8]
        logger.info("[%s] agent loop start — %d messages", sid, len(history))

        tool_schemas = self._get_tool_schemas(session_id)

        # Accumulate LLM usage stats across iterations
        total_completion_tokens = 0
        total_llm_elapsed = 0.0
        last_prompt_tokens = 0
        llm_calls = 0
        t_start = time.monotonic()

        def _finalize_stats() -> None:
            """Write accumulated LLM stats into metadata dict."""
            if metadata is None:
                return
            wall = time.monotonic() - t_start
            tps = total_completion_tokens / total_llm_elapsed if total_llm_elapsed > 0 else 0.0
            metadata["stats"] = {
                "context_tokens": last_prompt_tokens + total_completion_tokens,
                "last_prompt_tokens": last_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "completion_tps": round(tps, 1),
                "llm_calls": llm_calls,
                "llm_elapsed_sec": round(total_llm_elapsed, 2),
                "wall_elapsed_sec": round(wall, 2),
                "iterations": 0,  # filled at return site
            }

        for iteration in range(self.config.max_iterations):
            if stop_event and stop_event.is_set():
                logger.info("[%s] stop signal received, aborting agent loop", sid)
                _finalize_stats()
                if metadata and "stats" in metadata:
                    metadata["stats"]["iterations"] = iteration
                return "Session reset."

            # Proactive trim: if the previous call's prompt is past 85% of n_ctx,
            # drop the oldest half of the messages before sending the next request.
            if (
                self.config.n_ctx
                and last_prompt_tokens > TRIM_THRESHOLD * self.config.n_ctx
            ):
                target = max(1, int(len(history) * TRIM_FRACTION))
                logger.warning(
                    "[%s] proactive trim: prompt_tokens=%d > %.0f%% of n_ctx=%d, %d → %d msgs",
                    sid, last_prompt_tokens, TRIM_THRESHOLD * 100,
                    self.config.n_ctx, len(history), target,
                )
                _trim_history(history, target_messages=target)
                messages = [{"role": "system", "content": system_prompt}] + history

            logger.debug("[%s] iteration %d — calling LLM (%d messages)", sid, iteration + 1, len(messages))
            try:
                response = await call_llm(
                    self.config.model, messages, tool_schemas,
                    max_tokens=self.config.max_tokens,
                    api_base=self.config.api_base,
                    openrouter_provider=self.config.openrouter_provider,
                )
            except (litellm.ContextWindowExceededError, litellm.BadRequestError) as e:
                if not _is_context_overflow(e):
                    raise
                target = max(1, len(history) // 2)
                logger.warning("[%s] context window exceeded, trimming %d → %d messages", sid, len(history), target)
                _trim_history(history, target_messages=target)
                if not history:
                    return "Sorry, the message was too long for me to process."
                messages = [{"role": "system", "content": system_prompt}] + history
                try:
                    response = await call_llm(
                        self.config.model, messages, tool_schemas,
                        max_tokens=self.config.max_tokens,
                        api_base=self.config.api_base,
                        openrouter_provider=self.config.openrouter_provider,
                    )
                except (litellm.ContextWindowExceededError, litellm.BadRequestError) as e2:
                    if not _is_context_overflow(e2):
                        raise
                    logger.error("[%s] context window still exceeded after trim, aborting", sid)
                    return "Sorry, the conversation is too long. Please start a new thread."

            # Accumulate usage
            total_completion_tokens += response.usage.completion_tokens
            total_llm_elapsed += response.usage.elapsed_sec
            last_prompt_tokens = response.usage.prompt_tokens or 0
            llm_calls += 1

            if response.tool_calls:
                # Omit content when tool_calls are present: some thinking-mode
                # providers (e.g. GLM via DeepInfra) reject a trailing assistant
                # message with content as an incompatible "prefill".
                assistant_msg: dict = {"role": "assistant", "tool_calls": response.tool_calls}
                history.append(assistant_msg)

                for tool_call in response.tool_calls:
                    if stop_event and stop_event.is_set():
                        logger.info("[%s] stop signal received during tool execution", sid)
                        break

                    name = tool_call["function"]["name"]
                    args_str = tool_call["function"]["arguments"]
                    detail_lines = _tool_detail_lines(name, args_str)
                    logger.info("[%s] tool call: %s", sid, name)
                    for line in detail_lines:
                        logger.info("  %s", line)

                    if on_tool_call:
                        await on_tool_call(name, args_str)

                    result = await execute_tool_call(
                        name,
                        json.loads(args_str),
                        self.config,
                        attachments=attachments,
                        on_tool_call=on_tool_call,
                    )

                    # After load_skill, check for required tools in frontmatter
                    if name == "load_skill":
                        required = _parse_skill_tools(result)
                        if required:
                            self._session_tools.setdefault(session_id, set()).update(required)
                            tool_schemas = self._get_tool_schemas(session_id)
                            logger.info("[%s] skill loaded tools: %s", sid, required)

                    result_preview = result[:200] if result else "(empty)"
                    logger.debug("[%s] tool result: %s", sid, result_preview)
                    history.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": result,
                    })

                messages = [{"role": "system", "content": system_prompt}] + history
                continue

            if response.text:
                logger.info("[%s] agent done after %d iteration(s), response length: %d chars", sid, iteration + 1, len(response.text))
                history.append({"role": "assistant", "content": response.text})
                _finalize_stats()
                if metadata and "stats" in metadata:
                    metadata["stats"]["iterations"] = iteration + 1
                if system_task_prompt:
                    self.sessions.pop(session_id, None)
                return response.text

            logger.warning("[%s] LLM returned empty response", sid)
            history.append({"role": "assistant", "content": ""})
            _finalize_stats()
            if metadata and "stats" in metadata:
                metadata["stats"]["iterations"] = iteration + 1
            if system_task_prompt:
                self.sessions.pop(session_id, None)
            return "Error: LLM returned empty response."

        logger.warning("[%s] iteration limit reached (%d)", sid, self.config.max_iterations)
        _finalize_stats()
        if metadata and "stats" in metadata:
            metadata["stats"]["iterations"] = self.config.max_iterations
        if system_task_prompt:
            self.sessions.pop(session_id, None)
        return "Iteration limit reached."
