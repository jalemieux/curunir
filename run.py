"""Curunir runtime — configures channels, wires queues, starts the agent loop."""

import asyncio
import base64
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
            task = args.get("task", "")
            if len(task) > 60:
                task = task[:57] + "..."
            return f"Delegate: {task}"
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


def build_multimodal_content(text: str, attachments: list[dict] | None) -> str | list:
    """Build LiteLLM content from text + a staged-attachment manifest.

    Returns a plain `str` when there are no attachments (backward-compatible
    with the existing flow) or a list of content blocks otherwise.
    Images become `image_url` data-URI blocks; UTF-8 text files become
    fenced text blocks tagged with the filename.
    Anthropic rejects messages whose text blocks are empty, so when the user
    supplies no text we seed a minimal non-empty prompt referencing the files.
    """
    if not attachments:
        return text

    blocks: list[dict] = []
    # Always include a non-empty text block. Anthropic's API rejects empty
    # text blocks, and some vision models produce better responses when given
    # an explicit instruction alongside the image.
    if not text:
        names = ", ".join(att.get("filename", "file") for att in attachments)
        text = f"Please examine the attached file(s): {names}"
    blocks.append({"type": "text", "text": text})

    for att in attachments:
        mime = att["mime_type"]
        path = att["path"]
        if mime.startswith("image/"):
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        elif mime == "application/pdf":
            from pypdf import PdfReader
            reader = PdfReader(path)
            pages_text = "\n\n".join(p.extract_text() or "" for p in reader.pages)
            body = pages_text.strip() or "(no extractable text)"
            blocks.append({
                "type": "text",
                "text": (
                    f"[Attachment: {att['filename']} (PDF, {len(reader.pages)} pages)]\n"
                    f"```\n{body}\n```"
                ),
            })
        else:
            with open(path, "rb") as f:
                content = f.read().decode("utf-8")
            blocks.append({
                "type": "text",
                "text": f"[Attachment: {att['filename']}]\n```\n{content}\n```",
            })

    return blocks


async def _extract_and_record(agent: Agent, session_id: str, history: list[dict]):
    """Extract learnings for a session and record the archive path for reuse."""
    archive_path = agent.session_archives.get(session_id)
    written = await extract_learnings(agent.config, history, archive_path=archive_path)
    if written is not None:
        agent.session_archives[session_id] = written


async def agent_worker(agent: Agent, in_queue: asyncio.Queue, out_queue: asyncio.Queue):
    """Bridge between the message queues and the agent loop."""
    while True:
        msg = await in_queue.get()
        logger.info("Processing message from %s (session %s)", msg.channel, msg.session_id)

        if msg.command in ("clear", "reset"):
            history = agent.sessions.pop(msg.session_id, None)
            archive_path = agent.session_archives.pop(msg.session_id, None)
            if history:
                asyncio.create_task(extract_learnings(
                    agent.config, list(history), archive_path=archive_path,
                ))
            await out_queue.put(OutgoingMessage(
                content="", channel=msg.channel, session_id=msg.session_id,
                reply_address=msg.reply_address,
            ))
            continue

        if msg.command == "extract":
            history = agent.sessions.get(msg.session_id)
            if history:
                asyncio.create_task(_extract_and_record(
                    agent, msg.session_id, list(history),
                ))
            await out_queue.put(OutgoingMessage(
                content="", channel=msg.channel, session_id=msg.session_id,
                reply_address=msg.reply_address,
            ))
            continue

        async def on_tool_call(name: str, args_str: str):
            await out_queue.put(OutgoingMessage(
                content="",
                channel=msg.channel,
                session_id=msg.session_id,
                reply_address=msg.reply_address,
                tool_calls=[_summarize_tool_call(name, args_str)],
                final=False,
            ))

        async def on_text_delta(chunk: str):
            await out_queue.put(OutgoingMessage(
                content=chunk,
                channel=msg.channel,
                session_id=msg.session_id,
                reply_address=msg.reply_address,
                delta=True,
                final=False,
            ))

        content = build_multimodal_content(msg.content, msg.attachments)
        attachments = []
        metadata: dict = {}

        try:
            text = await agent.handle(
                content, msg.session_id,
                on_tool_call=on_tool_call, attachments=attachments,
                metadata=metadata,
                on_text_delta=on_text_delta,
            )
        except Exception as e:
            logger.error("Agent error for session %s: %s", msg.session_id, e)
            text = "Sorry, I encountered an error processing your message."

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
                asyncio.create_task(_extract_and_record(
                    agent, session_id, list(history),
                ))
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
