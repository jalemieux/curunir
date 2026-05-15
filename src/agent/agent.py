# src/agent/agent.py
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import litellm

from src.agent.system_prompt import build_static_prompt

logger = logging.getLogger(__name__)
from src.agent.loop_judge import (
    JudgeDecision,
    judge_loop_progress,
    summarize_recent_iterations,
)
from src.config import AgentConfig
from src.llm import call_llm, classify_provider_error
from src.skills import parse_frontmatter
from src.tools.dispatcher import execute_tool_call
from src.tools.schemas import get_tool_schemas
from src.usage_store import UsageRecord, UsageStore


_DEFAULT_MAX_HISTORY_CHARS = 250_000  # ~80k tokens, leaves room for system prompt + tool schemas + max_tokens
_IMAGE_COST_CHARS = 2000  # fixed budget per image block for history trimming

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
    "delegate": ["task"],
    "attach": ["path"],
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
        val_str = " ".join(str(val).split())
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


def _estimate_chars(messages: list[dict]) -> int:
    """Rough character count across all message contents.

    For list-form content (multimodal messages), text blocks count their
    text length and image blocks charge a fixed per-image cost so images
    age out of history alongside text on long sessions.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    total += len(str(block))
                    continue
                btype = block.get("type")
                if btype == "text":
                    total += len(block.get("text", ""))
                elif btype == "image_url":
                    total += _IMAGE_COST_CHARS
                else:
                    total += len(str(block))
    return total


def _trim_history(history: list[dict], max_chars: int = _DEFAULT_MAX_HISTORY_CHARS) -> None:
    """Remove oldest messages in coherent groups until under the char limit.

    Groups: user+assistant pairs, or assistant(tool_calls)+tool+...+tool sequences.
    Always removes from the front so the most recent context is preserved.
    Keeps at least one user message to avoid empty-messages API errors.
    When only one user message exists (e.g. system-task sessions), trims
    assistant+tool groups after it.
    """
    user_count = sum(1 for m in history if m["role"] == "user")

    if user_count > 1:
        # Multi-user-message session: trim by user message groups, keep at least one
        while user_count > 1 and _estimate_chars(history) > max_chars:
            if history[0]["role"] == "user":
                user_count -= 1
            history.pop(0)
            while history and history[0]["role"] != "user":
                history.pop(0)
    elif user_count == 1 and len(history) > 1:
        # Single user message (e.g. system task): keep first message,
        # trim assistant+tool groups after it
        while len(history) > 1 and _estimate_chars(history) > max_chars:
            del history[1]
            while len(history) > 1 and history[1]["role"] == "tool":
                del history[1]


def _is_context_overflow(exc: Exception) -> bool:
    """Check if an exception is a context window / input length overflow."""
    if isinstance(exc, litellm.ContextWindowExceededError):
        return True
    msg = str(exc).lower()
    return "context limit" in msg or "prompt is too long" in msg or "exceed" in msg and "token" in msg


def _parse_skill_tools(skill_content: str) -> list[str]:
    """Extract required tool names from a skill's frontmatter."""
    fm = parse_frontmatter(skill_content)
    tools_str = fm.get("tools", "")
    if not tools_str:
        return []
    return [t.strip() for t in tools_str.split(",") if t.strip()]


def _content_to_text_summary(content) -> str:
    """Flatten a message's content (string or multimodal list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
        return ""
    return str(content or "")


class Agent:
    def __init__(
        self,
        config: AgentConfig,
        tools: list[str] | None = None,
        usage_store: "UsageStore | None" = None,
    ):
        self.config = config
        self.sessions: dict[str, list[dict]] = {}
        self.session_archives: dict[str, Path] = {}
        # Bake the timestamp into the static prompt once at construction.
        # Auto-cache providers (OpenAI, DeepSeek, xAI, GLM via OpenRouter)
        # hash the prefix — a per-call timestamp would invalidate the cache
        # every iteration of the tool loop.
        self._boot_time = datetime.now()
        self.static_prompt = (
            build_static_prompt(config)
            + f"\n\nConversation started at: {self._boot_time.isoformat()}"
        )
        self.tools = tools  # None = all tools
        self._session_tools: dict[str, set[str]] = {}  # extra tools loaded by skills
        self.usage_store = usage_store
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._running_sessions: set[str] = set()

    def request_cancel(self, session_id: str) -> bool:
        """Signal an in-flight handle() to stop after the current iteration.

        Returns True if a session was running and the signal was delivered.
        """
        if session_id not in self._running_sessions:
            return False
        event = self._cancel_events.get(session_id)
        if event is None:
            return False
        event.set()
        return True

    def _get_tool_schemas(self, session_id: str | None = None) -> list[dict]:
        base = get_tool_schemas(self.tools)
        if session_id and session_id in self._session_tools:
            extra = get_tool_schemas(list(self._session_tools[session_id]))
            base = base + extra
        return base

    def history_snapshot(self, session_id: str = "portal") -> list[dict]:
        """Return a chat-shaped projection of conversation history for the portal.

        Walks self.sessions[session_id] once. Includes user turns and assistant
        turns; tool internals are summarized as one-liners (e.g. "bash: ls -la").
        Capped at 200 messages or 100 KB serialized.
        """
        import json as _json

        history = self.sessions.get(session_id, [])
        out: list[dict] = []
        for entry in history:
            role = entry.get("role")
            if role == "user":
                content = entry.get("content")
                if isinstance(content, list):
                    # Multimodal: only the first text block is the user's typed
                    # prompt; later text blocks are attachment-content wrappers
                    # ([Attachment: ...]\n```...```) injected by
                    # build_multimodal_content. Don't replay those into the
                    # user bubble.
                    text = next(
                        (p.get("text", "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text"),
                        "",
                    )
                else:
                    text = content or ""
                out.append({"role": "user", "content": text})
            elif role == "assistant":
                content = entry.get("content") or ""
                tool_calls = entry.get("tool_calls") or []
                summaries = []
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "tool")
                    args = fn.get("arguments", "")
                    if isinstance(args, str) and args:
                        try:
                            parsed = _json.loads(args)
                            first_val = next(iter(parsed.values()), "")
                            summaries.append(f"{name}: {first_val}")
                        except (ValueError, StopIteration):
                            summaries.append(name)
                    else:
                        summaries.append(name)
                out.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": summaries,
                })
            # role == "tool" is internal noise — skip.

        # Apply caps: 200 messages OR ~100 KB serialized, whichever first.
        while len(out) > 200 or len(_json.dumps(out)) > 100_000:
            if not out:
                break
            out.pop(0)

        return out

    async def handle(
        self, message: str | list, session_id: str,
        on_tool_call=None, attachments: list[dict] | None = None,
        system_task_prompt: str | None = None,
        metadata: dict | None = None,
        on_text_delta=None,
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
        _trim_history(history, max_chars=self.config.max_history_chars)
        messages = [{"role": "system", "content": system_prompt}] + history

        sid = session_id[:8]
        msg_chars = _estimate_chars(history)
        logger.info("[%s] agent loop start — %d messages, ~%dk chars", sid, len(history), msg_chars // 1000)

        cancel_event = self._cancel_events.setdefault(session_id, asyncio.Event())
        cancel_event.clear()
        self._running_sessions.add(session_id)

        tool_schemas = self._get_tool_schemas(session_id)

        # Accumulate LLM usage stats across iterations
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_cached_prompt_tokens = 0
        total_llm_elapsed = 0.0
        llm_calls = 0
        t_start = time.monotonic()

        def _finalize_stats() -> None:
            """Write accumulated LLM stats into metadata dict."""
            if metadata is None:
                return
            wall = time.monotonic() - t_start
            tps = total_completion_tokens / total_llm_elapsed if total_llm_elapsed > 0 else 0.0
            hit_rate = (
                total_cached_prompt_tokens / total_prompt_tokens
                if total_prompt_tokens > 0
                else 0.0
            )
            metadata["stats"] = {
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "cached_prompt_tokens": total_cached_prompt_tokens,
                "cache_hit_rate": round(hit_rate, 3),
                "total_tokens": total_prompt_tokens + total_completion_tokens,
                "llm_calls": llm_calls,
                "llm_elapsed_sec": round(total_llm_elapsed, 2),
                "wall_elapsed_sec": round(wall, 2),
                "completion_tps": round(tps, 1),
                "iterations": 0,  # filled at return site
            }

        async def _call_and_record():
            """Single LLM call + usage accumulation + context-overflow recovery.

            Returns (response, error_message). When error_message is non-None,
            the caller should return it to the user.
            """
            nonlocal total_prompt_tokens, total_completion_tokens
            nonlocal total_cached_prompt_tokens
            nonlocal total_llm_elapsed, llm_calls, messages
            try:
                resp = await call_llm(
                    self.config.model, messages, tool_schemas,
                    api_base=self.config.api_base,
                    openrouter_provider=self.config.openrouter_provider,
                    on_text_delta=on_text_delta,
                )
            except (litellm.ContextWindowExceededError, litellm.BadRequestError) as e:
                if not _is_context_overflow(e):
                    raise
                half = self.config.max_history_chars // 2
                logger.warning("[%s] context window exceeded, trimming history to %dk chars", sid, half // 1000)
                _trim_history(history, max_chars=half)
                if not history:
                    return None, "Sorry, the message was too long for me to process."
                messages = [{"role": "system", "content": system_prompt}] + history
                try:
                    resp = await call_llm(
                        self.config.model, messages, tool_schemas,
                        api_base=self.config.api_base,
                        openrouter_provider=self.config.openrouter_provider,
                        on_text_delta=on_text_delta,
                    )
                except (litellm.ContextWindowExceededError, litellm.BadRequestError) as e2:
                    if not _is_context_overflow(e2):
                        raise
                    logger.error("[%s] context window still exceeded after trim, aborting", sid)
                    return None, "Sorry, the conversation is too long. Please start a new thread."
            except Exception as e:
                classified = classify_provider_error(e)
                if classified is None:
                    raise
                category, user_message = classified
                logger.warning("[%s] provider error: %s", sid, category)
                return None, user_message

            total_prompt_tokens += resp.usage.prompt_tokens
            total_completion_tokens += resp.usage.completion_tokens
            total_cached_prompt_tokens += resp.usage.cached_prompt_tokens
            total_llm_elapsed += resp.usage.elapsed_sec
            llm_calls += 1

            if resp.usage.prompt_tokens > 0:
                cached_pct = round(
                    100 * resp.usage.cached_prompt_tokens / resp.usage.prompt_tokens
                )
                logger.debug(
                    "[%s] llm usage: prompt=%d completion=%d cached=%d%% elapsed=%.2fs",
                    sid,
                    resp.usage.prompt_tokens,
                    resp.usage.completion_tokens,
                    cached_pct,
                    resp.usage.elapsed_sec,
                )

            if self.usage_store is not None:
                record = UsageRecord(
                    ts=datetime.now(timezone.utc),
                    session_id=session_id,
                    model=resp.usage.model or self.config.model,
                    prompt_tokens=resp.usage.prompt_tokens,
                    completion_tokens=resp.usage.completion_tokens,
                    cached_prompt_tokens=resp.usage.cached_prompt_tokens,
                    reasoning_tokens=resp.usage.reasoning_tokens,
                    image_tokens=resp.usage.image_tokens,
                    audio_tokens=resp.usage.audio_tokens,
                    cost_usd=resp.usage.cost_usd,
                    elapsed_sec=resp.usage.elapsed_sec,
                )
                try:
                    await asyncio.to_thread(self.usage_store.record, record)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] usage_store.record failed: %s", sid, exc)

            return resp, None

        # Capture the user request that opened this turn — fed to the judge
        # if/when the iteration budget runs out.
        user_request_text = ""
        for entry in reversed(history):
            if entry.get("role") == "user":
                user_request_text = _content_to_text_summary(entry.get("content"))
                break

        budget = self.config.max_iterations
        extensions_used = 0
        last_judge_decision: JudgeDecision | None = None

        async def _run_judge_with_cancel() -> JudgeDecision | None:
            """Invoke the judge while watching the cancel event.

            Returns None if cancellation fired before/during the judge call;
            otherwise returns the judge's decision.
            """
            if cancel_event.is_set():
                return None
            judge_model = self.config.judge_model or self.config.model
            transcript = summarize_recent_iterations(
                history, last_n=self.config.judge_transcript_last_n,
            )
            judge_task = asyncio.create_task(judge_loop_progress(
                model=judge_model,
                api_base=self.config.api_base,
                openrouter_provider=self.config.openrouter_provider,
                user_request=user_request_text,
                recent_transcript=transcript,
                iterations_so_far=iteration,
                extension_number=extensions_used,
                tokens_so_far=total_prompt_tokens + total_completion_tokens,
            ))
            cancel_wait = asyncio.create_task(cancel_event.wait())
            try:
                done, pending = await asyncio.wait(
                    {judge_task, cancel_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for t in (judge_task, cancel_wait):
                    if not t.done():
                        t.cancel()
            if cancel_wait in done and judge_task not in done:
                # Drain the cancelled judge task so its CancelledError
                # is observed and doesn't leak as an unhandled exception.
                try:
                    await judge_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                return None
            return judge_task.result()

        try:
            iteration = 0
            while True:
                if cancel_event.is_set():
                    logger.info("[%s] interrupted by user after %d iteration(s)", sid, iteration)
                    history.append({"role": "assistant", "content": "(interrupted)"})
                    _finalize_stats()
                    if metadata and "stats" in metadata:
                        metadata["stats"]["iterations"] = iteration
                    if system_task_prompt:
                        self.sessions.pop(session_id, None)
                    return "(interrupted)"

                # Budget exhausted: invoke judge or surface final summary.
                if iteration >= budget:
                    if extensions_used >= self.config.judge_max_extensions:
                        logger.warning(
                            "[%s] iteration limit reached (budget=%d, extensions=%d/%d)",
                            sid, budget, extensions_used,
                            self.config.judge_max_extensions,
                        )
                        if last_judge_decision is not None:
                            final = (
                                f"I stopped before finishing. {last_judge_decision.rationale}\n\n"
                                f"What I attempted:\n{last_judge_decision.summary}"
                            )
                        else:
                            final = (
                                "I stopped before finishing. The agent reached its "
                                "iteration limit and the safety judge was not configured "
                                "to grant more iterations."
                            )
                        history.append({"role": "assistant", "content": final})
                        _finalize_stats()
                        if metadata and "stats" in metadata:
                            metadata["stats"]["iterations"] = iteration
                        if system_task_prompt:
                            self.sessions.pop(session_id, None)
                        return final

                    decision = await _run_judge_with_cancel()
                    if decision is None:
                        logger.info("[%s] judge cancelled by user", sid)
                        history.append({"role": "assistant", "content": "(interrupted)"})
                        _finalize_stats()
                        if metadata and "stats" in metadata:
                            metadata["stats"]["iterations"] = iteration
                        if system_task_prompt:
                            self.sessions.pop(session_id, None)
                        return "(interrupted)"

                    last_judge_decision = decision
                    if metadata is not None:
                        metadata.setdefault("judge_decisions", []).append({
                            "action": decision.action,
                            "rationale": decision.rationale,
                            "summary": decision.summary,
                            "iteration": iteration,
                            "extension_number": extensions_used,
                        })

                    if decision.action == "continue":
                        budget += self.config.judge_extension_size
                        extensions_used += 1
                        logger.info(
                            "[%s] judge: continue (ext %d/%d) — %s",
                            sid, extensions_used,
                            self.config.judge_max_extensions,
                            decision.rationale,
                        )
                        # Loop back to top to start the next iteration.
                        continue

                    # stop
                    logger.info("[%s] judge: stop — %s", sid, decision.rationale)
                    final = (
                        f"I stopped before finishing. {decision.rationale}\n\n"
                        f"What I attempted:\n{decision.summary}"
                    )
                    history.append({"role": "assistant", "content": final})
                    _finalize_stats()
                    if metadata and "stats" in metadata:
                        metadata["stats"]["iterations"] = iteration
                    if system_task_prompt:
                        self.sessions.pop(session_id, None)
                    return final

                logger.debug("[%s] iteration %d — calling LLM (%d messages)", sid, iteration + 1, len(messages))
                response, err = await _call_and_record()
                if err is not None:
                    return err

                # Empty response (no text, no tool_calls): a transient model
                # glitch that otherwise kills the session. Retry the same call
                # once; if still empty, append a "Continue." nudge and try once
                # more before giving up.
                if not response.tool_calls and not response.text:
                    logger.warning(
                        "[%s] empty LLM response (finish_reason=%s); retrying",
                        sid, response.finish_reason,
                    )
                    response, err = await _call_and_record()
                    if err is not None:
                        return err
                if not response.tool_calls and not response.text:
                    logger.warning(
                        "[%s] empty LLM response after retry (finish_reason=%s); nudging with 'Continue.'",
                        sid, response.finish_reason,
                    )
                    nudge = {"role": "user", "content": "Continue."}
                    history.append(nudge)
                    messages = [{"role": "system", "content": system_prompt}] + history
                    response, err = await _call_and_record()
                    if err is not None:
                        return err

                if response.tool_calls:
                    assistant_msg: dict = {"role": "assistant", "tool_calls": response.tool_calls}
                    if response.text:
                        assistant_msg["content"] = response.text
                    history.append(assistant_msg)

                    for tool_call in response.tool_calls:
                        name = tool_call["function"]["name"]
                        args_str = tool_call["function"]["arguments"]

                        # Mid-batch cancel: schema requires a tool response per
                        # tool_call, so stub remaining calls with "(interrupted)"
                        # rather than execute them. The outer-loop cancel check
                        # fires on the next iteration and exits cleanly.
                        if cancel_event.is_set():
                            logger.info("[%s] skipping tool call %s (interrupted)", sid, name)
                            history.append({
                                "role": "tool",
                                "tool_call_id": tool_call["id"],
                                "content": "(interrupted)",
                            })
                            continue

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

                    _trim_history(history, max_chars=self.config.max_history_chars)
                    messages = [{"role": "system", "content": system_prompt}] + history
                    iteration += 1
                    continue

                # No tool calls — final response, exit the loop.
                if response.text:
                    logger.info("[%s] agent done after %d iteration(s), response length: %d chars", sid, iteration + 1, len(response.text))
                    history.append({"role": "assistant", "content": response.text})
                    _finalize_stats()
                    if metadata and "stats" in metadata:
                        metadata["stats"]["iterations"] = iteration + 1
                    if system_task_prompt:
                        self.sessions.pop(session_id, None)
                    return response.text

                history.append({"role": "assistant", "content": ""})
                _finalize_stats()
                if metadata and "stats" in metadata:
                    metadata["stats"]["iterations"] = iteration + 1
                if system_task_prompt:
                    self.sessions.pop(session_id, None)
                # Empty text is fine when the agent already attached a file this
                # turn — the attachment is the reply.
                if attachments:
                    logger.info("[%s] agent done with attachment-only reply", sid)
                    return ""
                logger.warning("[%s] LLM returned empty response", sid)
                return "Error: LLM returned empty response."
        finally:
            self._running_sessions.discard(session_id)
            cancel_event.clear()
