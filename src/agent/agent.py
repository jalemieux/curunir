# src/agent/agent.py
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import litellm

from src.agent import conversation_store
from src.agent.scratch import is_scratch
from src.agent.system_prompt import build_memory_block, build_static_prompt

logger = logging.getLogger(__name__)
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

    Assistant tool-call `arguments` are counted too: write/edit calls embed
    entire file bodies there, and (unlike tool results) arguments are never
    capped, so they must be visible to the trimmer or write/edit-heavy
    sessions silently over-budget. Access is defensive so a malformed or
    absent function/arguments never raises inside the accounting path.
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
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function")
            if isinstance(fn, dict):
                total += len(fn.get("arguments") or "")
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


def _cap_tool_result(content: str, max_chars: int) -> str:
    """Cap a single tool result before it enters history (defense-in-depth).

    `_trim_history` only drops whole message groups from the front; it cannot
    shrink one oversized message, and a giant tool result is always the newest
    message. An uncapped read/bash/web_fetch result larger than the context
    window therefore crashes the turn and (interactively) poisons the session.
    This per-result cap turns that hard crash into a recoverable, model-actionable
    truncation. Slicing by character is safe — `content` is already decoded text.
    """
    if content is None or len(content) <= max_chars:
        return content
    dropped = len(content) - max_chars
    marker = (
        f"\n\n[... truncated {dropped} chars — "
        "use read offset/limit, or grep to narrow, before reading again ...]"
    )
    return content[:max_chars] + marker


def _arg_parse_error_message(name: str, args_str: str, exc: Exception, max_chars: int) -> str:
    """Build a corrective, model-visible message for a tool call whose
    ``arguments`` string wasn't valid JSON.

    The thin "retry with well-formed JSON" hint was too easy to ignore — in
    session e8ce5d0a the model abandoned a truncated ``portfolio set`` call and
    escalated into ``bash`` + source-diving instead of re-emitting it. This
    makes the correction actionable: it (1) echoes the *raw* argument string the
    model actually sent (capped, so a huge blob can't re-bloat history) so a
    truncation is visible at the cut point, (2) explicitly tells the model to
    re-emit the *same* call rather than switch tools, and (3) appends the tool's
    parameter schema so the retry matches the expected fields. We deliberately do
    NOT try to "repair"/complete the truncated JSON — guessing the missing tail
    could silently execute a half-specified write on a tool like ``portfolio``.
    """
    lines = [
        f"Error: the arguments you sent for tool '{name}' were not valid JSON ({exc}).",
        "",
        "What you sent (raw — a JSON syntax error here usually means it was cut off):",
        _cap_tool_result(args_str, max_chars),
        "",
        (
            f"Re-emit the SAME `{name}` tool call with complete, well-formed JSON "
            "arguments. Do not switch to another tool, and do not investigate this "
            "failure — just resend the call with valid JSON."
        ),
    ]

    schema = get_tool_schemas([name])
    params = schema[0]["function"].get("parameters") if schema else None
    if params:
        lines += [
            "",
            f"Expected argument schema for `{name}`:",
            json.dumps(params, indent=2),
        ]

    return "\n".join(lines)


def _is_context_overflow(exc: Exception) -> bool:
    """Check if an exception is a context window / input length overflow."""
    if isinstance(exc, litellm.ContextWindowExceededError):
        return True
    msg = str(exc).lower()
    return (
        "context limit" in msg
        or "prompt is too long" in msg
        or "maximum context length" in msg
        or "you requested about" in msg
        or ("exceed" in msg and "token" in msg)
    )


def _parse_skill_tools(skill_content: str) -> list[str]:
    """Extract required tool names from a skill's frontmatter."""
    fm = parse_frontmatter(skill_content)
    tools_str = fm.get("tools", "")
    if not tools_str:
        return []
    return [t.strip() for t in tools_str.split(",") if t.strip()]


class Agent:
    def __init__(
        self,
        config: AgentConfig,
        tools: list[str] | None = None,
        usage_store: "UsageStore | None" = None,
    ):
        self.config = config
        # Active sessions only — past conversations live on disk in
        # context/conversations/ and are lazy-loaded on access. The archive
        # path for memory extraction is tracked per-conversation on disk
        # (conversation_store metadata), not in memory.
        self.sessions: dict[str, list[dict]] = {}
        # The static prefix carries no timestamp — it must be byte-stable across
        # every session and the whole process lifetime so auto-cache providers
        # (OpenAI, DeepSeek, xAI, GLM via OpenRouter) keep hitting the prefix
        # cache. Time enters as two correctly-scoped signals instead: a stable
        # per-session "Conversation started at" line (added in
        # _get_session_prompt) and a live per-turn "Current date/time" note
        # injected outside the cached prefix in handle().
        self.static_prompt = build_static_prompt(config)
        logger.info(
            "system prompt prefix size: %d chars (identity + skill manifest)",
            len(self.static_prompt),
        )
        self.tools = tools  # None = all tools
        self._session_tools: dict[str, set[str]] = {}  # extra tools loaded by skills
        # Per-session memory snapshot (README.md + profile.md). Built on the
        # first turn of a session and reused for the rest of that session so
        # auto-cache providers keep hitting the prefix cache across the tool
        # loop. External edits during a session are picked up next session.
        self._session_prompts: dict[str, str] = {}
        # First-turn fallback timestamp for brand-new sessions that have not
        # been persisted yet (conversation_store.save runs after the turn).
        # Captured once per session so the started-at line stays stable.
        self._session_started_at_cache: dict[str, str] = {}
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

    def _session_started_at(self, session_id: str) -> str:
        """Stable ISO-8601 "started at" for a session (tz-aware).

        Sourced from the persisted conversation's ``created_at`` (set once and
        preserved across resume). Brand-new sessions have no record yet — the
        turn-completion save runs after handle() — so fall back to a first-turn
        timestamp captured once in memory and reused for the rest of the
        session, keeping the line byte-stable.
        """
        record = conversation_store.load(self.config.context_dir, session_id)
        if record and record.get("created_at"):
            return record["created_at"]
        started = self._session_started_at_cache.get(session_id)
        if started is None:
            started = datetime.now().astimezone().isoformat()
            self._session_started_at_cache[session_id] = started
        return started

    def _get_session_prompt(self, session_id: str) -> str:
        """System prompt for a session: static prefix + memory snapshot +
        a stable "Conversation started at" line.

        The memory block (memory/README.md + memory/profile.md) and the
        started-at line are computed once per session and cached so the system
        prompt stays byte-stable across turns within a session — required for
        auto-cache providers (OpenAI, DeepSeek, xAI, GLM via OpenRouter) to keep
        hitting the prefix cache during the tool loop. External file edits
        during a session are picked up on the next session start. The live
        per-turn "Current date/time" signal is *not* here — it rides outside
        the cached prefix as a trailing note in handle().
        """
        cached = self._session_prompts.get(session_id)
        if cached is not None:
            return cached

        block = build_memory_block(self.config.context_dir)
        started_line = f"Conversation started at: {self._session_started_at(session_id)}"
        parts = [self.static_prompt]
        if block:
            parts.append(block)
        parts.append(started_line)
        prompt = "\n\n".join(parts)
        self._session_prompts[session_id] = prompt
        return prompt

    def _get_tool_schemas(self, session_id: str | None = None) -> list[dict]:
        base = get_tool_schemas(self.tools)
        if session_id and session_id in self._session_tools:
            extra = get_tool_schemas(list(self._session_tools[session_id]))
            base = base + extra
        return base

    def _load_history(self, session_id: str) -> list[dict]:
        """Conversation history for a session.

        Returns the in-memory history for an active session, else lazily
        reads the persisted transcript from context/conversations/, else an
        empty list. A pure read — does not populate self.sessions.
        """
        if session_id in self.sessions:
            return self.sessions[session_id]
        record = conversation_store.load(self.config.context_dir, session_id)
        return record["history"] if record else []

    def conversations_snapshot(self) -> list[dict]:
        """Metadata-only summaries for the portal sidebar, newest first.

        Email-channel conversations are excluded — their transcripts live on
        disk for memory extraction but don't belong in the web sidebar. A
        missing ``channel`` (legacy records) is treated as not-email so
        existing web/CLI conversations keep showing.

        The ephemeral Scratch slot is also excluded — it has its own pinned
        slot in the portal and must never appear as a saved row, even if a
        transcript file ever leaked to disk.
        """
        return [
            c for c in conversation_store.list_conversations(self.config.context_dir)
            if c.get("channel") != "email" and not is_scratch(c.get("session_id"))
        ]

    def history_snapshot(self, session_id: str = "portal") -> list[dict]:
        """Return a chat-shaped projection of conversation history for the portal.

        Reads the session's history — from memory if active, otherwise lazily
        from the persisted transcript. Includes user turns and assistant
        turns; tool internals are summarized as one-liners (e.g. "bash: ls -la").
        Capped at 200 messages or 100 KB serialized.
        """
        import json as _json

        history = self._load_history(session_id)
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
                attachments: list[dict] = []
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "tool")
                    args = fn.get("arguments", "")
                    parsed = None
                    if isinstance(args, str) and args:
                        try:
                            parsed = _json.loads(args)
                        except ValueError:
                            parsed = None
                    if isinstance(parsed, dict):
                        first_val = next(iter(parsed.values()), "")
                        summaries.append(f"{name}: {first_val}")
                    else:
                        summaries.append(name)
                    # Rebuild attachments from `attach` calls already in the
                    # transcript so a reopened conversation keeps its files —
                    # outbound attachments are never persisted in history.
                    if name == "attach" and isinstance(parsed, dict) and parsed.get("path"):
                        from src.tools.attach import attachment_metadata
                        attachments.append(
                            attachment_metadata(parsed["path"], parsed.get("name"))
                        )
                msg = {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": summaries,
                }
                if attachments:
                    msg["attachments"] = attachments
                out.append(msg)
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
        # Lazy-load a persisted transcript when resuming a conversation that
        # is not currently in memory — agent.sessions holds active sessions
        # only, so a past conversation must be rehydrated on first access.
        if session_id not in self.sessions:
            record = conversation_store.load(self.config.context_dir, session_id)
            if record is not None:
                self.sessions[session_id] = record["history"]
        history = self.sessions.setdefault(session_id, [])

        # Onboarding gate: first user turn of a fresh session, no identity.md,
        # not a scheduled task → rewrite the message into an instruction that
        # kicks off the onboarding orchestrator. identity.md is the personality
        # layer, decoupled from behavior/persona; its absence is exactly the
        # "not yet onboarded" signal (onboarding writes it). Mid-flow turns have
        # non-empty history and pass through unchanged.
        if (
            system_task_prompt is None
            and len(history) == 0
            and not self.config.identity_file.exists()
        ):
            message = (
                "The user has just connected and isn't onboarded yet. "
                "Open with a one-line preamble like 'Since you're new, "
                "let's get you set up — about a minute.' Then use the "
                "`onboarding` skill to walk them through it."
            )

        if system_task_prompt:
            # System-initiated task: inject task as a user message so all LLM
            # providers accept the request (some reject system-only conversations).
            history.append({"role": "user", "content": f"## Scheduled Task\n{system_task_prompt}"})
        else:
            history.append({"role": "user", "content": message})
        system_prompt = self._get_session_prompt(session_id)

        # Live per-turn "now", computed once at turn start and injected as a
        # trailing, non-persisted note. It sits *outside* the cacheable prefix
        # (system + history) and is identical across this turn's tool-loop
        # iterations, so it gives the model a fresh clock without busting the
        # prefix cache. tz-aware (.astimezone()) so the offset is explicit.
        now = datetime.now().astimezone()
        live_time_note = {
            "role": "user",
            "content": f"Current date/time: {now.isoformat()} ({now.strftime('%A')})",
        }

        def _assemble_messages() -> list[dict]:
            """[system] + history + live-time note. The note is never written
            into history — it is appended only at LLM-call assembly time."""
            return [{"role": "system", "content": system_prompt}] + history + [live_time_note]

        _trim_history(history, max_chars=self.config.max_history_chars)
        messages = _assemble_messages()

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
                messages = _assemble_messages()
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

        try:
            for iteration in range(self.config.max_iterations):
                if cancel_event.is_set():
                    logger.info("[%s] interrupted by user after %d iteration(s)", sid, iteration)
                    history.append({"role": "assistant", "content": "(interrupted)"})
                    _finalize_stats()
                    if metadata and "stats" in metadata:
                        metadata["stats"]["iterations"] = iteration
                    if system_task_prompt:
                        self.sessions.pop(session_id, None)
                        self._session_prompts.pop(session_id, None)
                    return "(interrupted)"
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
                    messages = _assemble_messages()
                    response, err = await _call_and_record()
                    if err is not None:
                        return err

                if response.tool_calls:
                    assistant_msg: dict = {"role": "assistant", "tool_calls": response.tool_calls}
                    if response.text:
                        assistant_msg["content"] = response.text
                    history.append(assistant_msg)

                    # Run the batch's tool calls concurrently. The chat schema
                    # requires exactly one tool response per tool_call; asyncio.gather
                    # preserves input order, so results map back 1:1.
                    #
                    # Cancellation: mid-batch cancel can no longer skip "remaining"
                    # calls — once gathered, every call in the batch is in flight at
                    # once and runs to completion. A cancel requested before the
                    # batch starts still stubs the whole batch with "(interrupted)";
                    # the outer-loop cancel check fires on the next iteration.
                    async def _run_tool_call(tool_call: dict) -> dict:
                        name = tool_call["function"]["name"]
                        args_str = tool_call["function"]["arguments"]

                        if cancel_event.is_set():
                            logger.info("[%s] skipping tool call %s (interrupted)", sid, name)
                            return {
                                "role": "tool",
                                "tool_call_id": tool_call["id"],
                                "content": _cap_tool_result("(interrupted)", self.config.max_tool_result_chars),
                                "_tool_name": name,
                            }

                        detail_lines = _tool_detail_lines(name, args_str)
                        logger.info("[%s] tool call: %s", sid, name)
                        for line in detail_lines:
                            logger.info("  %s", line)

                        if on_tool_call:
                            await on_tool_call(name, args_str)

                        try:
                            args = json.loads(args_str)
                        except (json.JSONDecodeError, TypeError) as exc:
                            logger.warning("[%s] tool %s: unparseable arguments: %s", sid, name, exc)
                            return {
                                "role": "tool",
                                "tool_call_id": tool_call["id"],
                                "content": _arg_parse_error_message(
                                    name, args_str, exc, self.config.max_tool_result_chars
                                ),
                                "_tool_name": name,
                            }

                        try:
                            result = await execute_tool_call(
                                name,
                                args,
                                self.config,
                                attachments=attachments,
                                on_tool_call=on_tool_call,
                            )
                        except Exception as exc:
                            # Systemic backstop: no single tool's unanticipated
                            # exception may crash the turn. asyncio.gather runs
                            # with return_exceptions=False, so a raise here would
                            # propagate out of handle() and kill the session.
                            # Turn it into a model-visible tool error instead.
                            logger.warning("[%s] tool %s raised: %s", sid, name, exc)
                            return {
                                "role": "tool",
                                "tool_call_id": tool_call["id"],
                                "content": _cap_tool_result(
                                    f"Error: tool '{name}' failed: {exc}",
                                    self.config.max_tool_result_chars,
                                ),
                                "_tool_name": name,
                            }

                        result_preview = result[:200] if result else "(empty)"
                        logger.debug("[%s] tool result: %s", sid, result_preview)
                        return {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": _cap_tool_result(result, self.config.max_tool_result_chars),
                            "_tool_name": name,
                        }

                    tool_messages = await asyncio.gather(
                        *(_run_tool_call(tc) for tc in response.tool_calls)
                    )

                    for tool_msg in tool_messages:
                        # After load_skill, check for required tools in frontmatter.
                        # Done post-gather so concurrent skill loads apply in order.
                        if tool_msg.pop("_tool_name", None) == "load_skill":
                            required = _parse_skill_tools(tool_msg["content"])
                            if required:
                                self._session_tools.setdefault(session_id, set()).update(required)
                                tool_schemas = self._get_tool_schemas(session_id)
                                logger.info("[%s] skill loaded tools: %s", sid, required)
                        history.append(tool_msg)

                    _trim_history(history, max_chars=self.config.max_history_chars)
                    messages = _assemble_messages()
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
                        self._session_prompts.pop(session_id, None)
                    return response.text

                history.append({"role": "assistant", "content": ""})
                _finalize_stats()
                if metadata and "stats" in metadata:
                    metadata["stats"]["iterations"] = iteration + 1
                if system_task_prompt:
                    self.sessions.pop(session_id, None)
                    self._session_prompts.pop(session_id, None)
                # Empty text is fine when the agent already attached a file this
                # turn — the attachment is the reply.
                if attachments:
                    logger.info("[%s] agent done with attachment-only reply", sid)
                    return ""
                logger.warning("[%s] LLM returned empty response", sid)
                return "Error: LLM returned empty response."

            logger.warning("[%s] iteration limit reached (%d)", sid, self.config.max_iterations)
            _finalize_stats()
            if metadata and "stats" in metadata:
                metadata["stats"]["iterations"] = self.config.max_iterations
            if system_task_prompt:
                self.sessions.pop(session_id, None)
                self._session_prompts.pop(session_id, None)
            return "Iteration limit reached."
        finally:
            self._running_sessions.discard(session_id)
            cancel_event.clear()
