"""Curunir runtime — configures channels, wires queues, starts the agent loop."""

import asyncio
import json
import logging
import os

import httpx
from dotenv import load_dotenv

from src.agent.agent import Agent
from src.channels.base import OutgoingMessage
from src.channels.email import EmailChannel
from src.channels.ws import WebSocketChannel
from src.channels.router import route_outbound
from src.config import AgentConfig, EmailChannelConfig
from src.bootstrap import bootstrap_context
from src.memory_extractor import extract_learnings
from src.scheduler import run_scheduler


def _summarize_tool_call(name: str, args_str: str) -> str:
    """Format a tool call for display, e.g. 'Read src/config.py'."""
    try:
        args = json.loads(args_str)
    except json.JSONDecodeError:
        return name

    match name:
        case "read":
            return f"Read {args.get('file_path', '')}"
        case "write":
            return f"Write {args.get('file_path', '')}"
        case "edit":
            return f"Edit {args.get('file_path', '')}"
        case "glob":
            return f"Glob {args.get('pattern', '')}"
        case "grep":
            return f"Grep pattern={args.get('pattern', '')!r}"
        case "bash":
            cmd = args.get("command", "")
            if len(cmd) > 60:
                cmd = cmd[:57] + "..."
            return f"Bash {cmd}"
        case "load_skill":
            return f"LoadSkill {args.get('name', '')}"
        case "web_fetch":
            return f"WebFetch {args.get('url', '')}"
        case "delegate":
            agent_name = args.get("agent", "")
            task = args.get("task", "")
            if len(task) > 50:
                task = task[:47] + "..."
            return f"Delegate [{agent_name}]: {task}"
        case "attach":
            name = args.get("name") or args.get("path", "")
            return f"Attach {name}"
        case "schedule":
            action = args.get("action", "")
            task_id = args.get("id", "")
            return f"Schedule {action} {task_id}".strip()
        case _:
            return f"{name} {args_str}"


async def _fetch_llamacpp_stats(api_base: str) -> dict | None:
    """Query llama.cpp /slots endpoint for KV cache and slot stats."""
    # llama.cpp serves /slots at the root, not under /v1/
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(api_base)
    url = urlunparse(parsed._replace(path="/slots"))
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            slots = resp.json()
            if not slots:
                return None
            # Aggregate across all slots
            result: dict = {"slots": []}
            for slot in slots:
                s: dict = {
                    "id": slot.get("id"),
                    "state": slot.get("state"),
                    "n_ctx": slot.get("n_ctx"),
                    "n_predict": slot.get("n_predict"),
                    "n_past": slot.get("n_past"),
                }
                # Prompt eval and generation timing from the slot
                prompt_ms = slot.get("t_prompt_processing")
                gen_ms = slot.get("t_token_generation")
                n_prompt = slot.get("n_prompt_tokens_processed")
                n_gen = slot.get("n_tokens_predicted")
                if prompt_ms and n_prompt:
                    s["prompt_tps"] = round(n_prompt / (prompt_ms / 1000), 1)
                    s["prompt_tokens_processed"] = n_prompt
                if gen_ms and n_gen:
                    s["generation_tps"] = round(n_gen / (gen_ms / 1000), 1)
                    s["tokens_predicted"] = n_gen
                result["slots"].append(s)
            return result
    except Exception as e:
        logger.debug("Failed to fetch llama.cpp stats: %s", e)
        return None


_MAX_ATTACHMENT_CONTENT_SIZE = 512 * 1024  # 512KB


def _enrich_attachments(attachments: list[dict], project_root: str) -> None:
    """Add content and normalize paths for text-based attachments in-place."""
    for att in attachments:
        path = att["path"]
        mime = att.get("mime_type", "")
        is_text = mime.startswith("text/") or mime == "application/json"

        # Normalize path to relative
        if os.path.isabs(path):
            try:
                att["path"] = os.path.relpath(path, project_root)
            except ValueError:
                pass  # different drive on Windows, keep absolute

        if not is_text:
            att["content"] = None
            continue

        if not os.path.isfile(path):
            att["content"] = None
            att["error"] = "file not found"
            continue

        if os.path.getsize(path) > _MAX_ATTACHMENT_CONTENT_SIZE:
            att["content"] = None
            continue

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                att["content"] = f.read()
        except OSError:
            att["content"] = None
            att["error"] = "file not found"


def _build_content(msg) -> str:
    """Build LLM content from a message.

    Prepends channel metadata so the agent knows the source and sender.
    Attachments are referenced by file path in msg.content (added by the
    channel). The agent uses the delegate tool to analyze images and
    large documents in a sub-agent with a clean context window.
    """
    parts = []
    if msg.channel and msg.channel != "cli":
        meta = f"[channel: {msg.channel}"
        if msg.reply_address:
            sender = msg.reply_address.get("to", "")
            if sender:
                meta += f", from: {sender}"
        meta += "]"
        parts.append(meta)
    parts.append(msg.content)
    return "\n".join(parts)


async def agent_worker(agent: Agent, in_queue: asyncio.Queue, out_queue: asyncio.Queue):
    """Bridge between the message queues and the agent loop."""
    pending: list = []  # messages received while handle() was running

    while True:
        if pending:
            msg = pending.pop(0)
        else:
            msg = await in_queue.get()
        logger.info("Processing message from %s (session %s)", msg.channel, msg.session_id)

        if msg.command in ("clear", "reset"):
            history = agent.sessions.pop(msg.session_id, None)
            if history and msg.command == "clear":
                asyncio.create_task(extract_learnings(agent.config, list(history)))
            await out_queue.put(OutgoingMessage(
                content="", channel=msg.channel, session_id=msg.session_id,
                reply_address=msg.reply_address,
            ))
            continue

        if msg.command == "extract":
            history = agent.sessions.get(msg.session_id)
            if history:
                asyncio.create_task(extract_learnings(agent.config, list(history)))
            await out_queue.put(OutgoingMessage(
                content="", channel=msg.channel, session_id=msg.session_id,
                reply_address=msg.reply_address,
            ))
            continue

        stop_event = asyncio.Event()

        async def on_tool_call(name: str, args_str: str):
            await out_queue.put(OutgoingMessage(
                content="",
                channel=msg.channel,
                session_id=msg.session_id,
                reply_address=msg.reply_address,
                tool_calls=[_summarize_tool_call(name, args_str)],
                final=False,
            ))

        content = _build_content(msg)
        attachments = []
        metadata: dict = {}

        handle_task = asyncio.create_task(
            agent.handle(
                content, msg.session_id,
                on_tool_call=on_tool_call, attachments=attachments,
                metadata=metadata, stop_event=stop_event,
            )
        )

        # Monitor queue for reset/clear while handle() runs
        reset_msg = None
        queue_task = asyncio.create_task(in_queue.get())
        try:
            while not handle_task.done():
                done, _ = await asyncio.wait(
                    {handle_task, queue_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if queue_task in done:
                    next_msg = queue_task.result()
                    if next_msg.command in ("clear", "reset") and next_msg.session_id == msg.session_id:
                        stop_event.set()
                        reset_msg = next_msg
                        break
                    pending.append(next_msg)
                    queue_task = asyncio.create_task(in_queue.get())
        finally:
            if not queue_task.done():
                queue_task.cancel()
                try:
                    await queue_task
                except asyncio.CancelledError:
                    pass

        try:
            text = await handle_task
        except Exception as e:
            logger.error("Agent error for session %s: %s", msg.session_id, e)
            text = "Sorry, I encountered an error processing your message."

        if reset_msg:
            # handle() was interrupted — process the reset
            history = agent.sessions.pop(msg.session_id, None)
            if history and reset_msg.command == "clear":
                asyncio.create_task(extract_learnings(agent.config, list(history)))
            await out_queue.put(OutgoingMessage(
                content="", channel=reset_msg.channel, session_id=reset_msg.session_id,
                reply_address=reset_msg.reply_address,
            ))
            continue

        if attachments:
            _enrich_attachments(attachments, os.getcwd())

        # Fetch llama.cpp server stats if using a local API base
        if agent.config.api_base and metadata.get("stats"):
            llama_stats = await _fetch_llamacpp_stats(agent.config.api_base)
            if llama_stats:
                metadata["stats"]["server"] = llama_stats

        await out_queue.put(OutgoingMessage(
            content=text,
            channel=msg.channel,
            session_id=msg.session_id,
            reply_address=msg.reply_address,
            attachments=attachments or None,
            workflow=metadata.get("workflow"),
            stats=metadata.get("stats"),
        ))


async def periodic_extraction(agent: Agent, interval_sec: int):
    """Periodically extract learnings from sessions that have grown."""
    last_extracted_len: dict[str, int] = {}
    while True:
        await asyncio.sleep(interval_sec)
        for session_id, history in agent.sessions.items():
            prev_len = last_extracted_len.get(session_id, 0)
            if len(history) > prev_len:
                asyncio.create_task(extract_learnings(agent.config, list(history)))
                last_extracted_len[session_id] = len(history)


logger = logging.getLogger(__name__)


async def main():
    load_dotenv()
    log_file = os.environ.get("LOG_FILE", "")
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        **({"filename": log_file} if log_file else {}),
    )
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    model = os.environ.get("MODEL")
    api_base = os.environ.get("API_BASE")
    openrouter_provider = os.environ.get("OPENROUTER_PROVIDER")
    max_history_chars = os.environ.get("MAX_HISTORY_CHARS")
    config = AgentConfig(
        **({"model": model} if model else {}),
        **({"api_base": api_base} if api_base else {}),
        **({"openrouter_provider": openrouter_provider} if openrouter_provider else {}),
        **({"max_history_chars": int(max_history_chars)} if max_history_chars else {}),
    )

    bootstrap_context(config.context_dir)
    agent = Agent(config)
    in_queue = asyncio.Queue()
    out_queue = asyncio.Queue()

    # Register channels
    channels = {}
    ws_host = os.environ.get("WS_HOST", "0.0.0.0")
    ws_port = int(os.environ.get("WS_PORT", "8765"))
    ws = WebSocketChannel(in_queue, host=ws_host, port=ws_port, model=config.model)
    channels["cli"] = ws

    # Email channel (conditional)
    email_config = EmailChannelConfig(
        enabled=os.environ.get("EMAIL_ENABLED", "false").lower() == "true",
        service_account_file=os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", ""),
        delegated_user=os.environ.get("GOOGLE_DELEGATED_USER", ""),
        poll_interval_sec=int(os.environ.get("EMAIL_POLL_INTERVAL", "300")),
        allowed_senders=[s.strip() for s in os.environ.get("EMAIL_ALLOWED_SENDERS", "").split(",") if s.strip()],
        processed_label=os.environ.get("EMAIL_PROCESSED_LABEL", "agent/processed"),
        attachment_dir=os.environ.get("EMAIL_ATTACHMENT_DIR", "/tmp/attachments"),
    )
    if email_config.enabled:
        email_channel = EmailChannel(in_queue, email_config)
        channels["email"] = email_channel
        logger.info("Email channel enabled for %s (poll every %ds)", email_config.delegated_user, email_config.poll_interval_sec)

    extraction_interval = int(os.environ.get("EXTRACTION_INTERVAL_SEC", "3600"))

    logger.info("Starting %d channel(s): %s", len(channels), ", ".join(channels.keys()))

    # Start all channels, the router, and the agent worker
    async with asyncio.TaskGroup() as tg:
        for channel in channels.values():
            tg.create_task(channel.start())
        tg.create_task(route_outbound(out_queue, channels))
        tg.create_task(agent_worker(agent, in_queue, out_queue))
        tg.create_task(periodic_extraction(agent, extraction_interval))
        tg.create_task(run_scheduler(agent))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        pass
