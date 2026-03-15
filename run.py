"""Curunir runtime — configures channels, wires queues, starts the agent loop."""

import asyncio
import json
import logging
import os

from dotenv import load_dotenv

from src.agent.agent import Agent
from src.channels.base import OutgoingMessage
from src.channels.cli import CLIChannel
from src.channels.email import EmailChannel
from src.channels.router import route_outbound
from src.config import AgentConfig, EmailChannelConfig
from src.memory_extractor import extract_learnings


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
        case "delegate":
            task = args.get("task", "")
            if len(task) > 60:
                task = task[:57] + "..."
            return f"Delegate: {task}"
        case _:
            return f"{name} {args_str}"


def _build_content(msg) -> str:
    """Build LLM content from a message.

    Attachments are referenced by file path in msg.content (added by the
    channel). The agent uses the delegate tool to analyze images and
    large documents in a sub-agent with a clean context window.
    """
    return msg.content


async def agent_worker(agent: Agent, in_queue: asyncio.Queue, out_queue: asyncio.Queue):
    """Bridge between the message queues and the agent loop."""
    while True:
        msg = await in_queue.get()
        logger.info("Processing message from %s (session %s)", msg.channel, msg.session_id)

        if msg.command == "clear":
            history = agent.sessions.pop(msg.session_id, None)
            if history:
                asyncio.create_task(extract_learnings(agent.config, list(history)))
            continue

        if msg.command == "extract":
            history = agent.sessions.get(msg.session_id)
            if history:
                asyncio.create_task(extract_learnings(agent.config, list(history)))
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

        content = _build_content(msg)
        try:
            text = await agent.handle(content, msg.session_id, on_tool_call=on_tool_call)
        except Exception as e:
            logger.error("Agent error for session %s: %s", msg.session_id, e)
            text = "Sorry, I encountered an error processing your message."

        await out_queue.put(OutgoingMessage(
            content=text,
            channel=msg.channel,
            session_id=msg.session_id,
            reply_address=msg.reply_address,
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
    config = AgentConfig()

    agent = Agent(config)
    in_queue = asyncio.Queue()
    out_queue = asyncio.Queue()

    # Register channels
    channels = {}
    cli_enabled = os.environ.get("CLI_ENABLED", "true").lower() == "true"
    if cli_enabled:
        cli = CLIChannel(in_queue, model=config.model)
        channels["cli"] = cli

    # Email channel (conditional)
    email_config = EmailChannelConfig(
        enabled=os.environ.get("EMAIL_ENABLED", "false").lower() == "true",
        account=os.environ.get("GOG_ACCOUNT", ""),
        poll_interval_sec=int(os.environ.get("EMAIL_POLL_INTERVAL", "60")),
        allowed_senders=[s.strip() for s in os.environ.get("EMAIL_ALLOWED_SENDERS", "").split(",") if s.strip()],
        processed_label=os.environ.get("EMAIL_PROCESSED_LABEL", "agent/processed"),
        attachment_dir=os.environ.get("EMAIL_ATTACHMENT_DIR", "/tmp/attachments"),
    )
    if email_config.enabled:
        email_channel = EmailChannel(in_queue, email_config)
        channels["email"] = email_channel
        logger.info("Email channel enabled for %s (poll every %ds)", email_config.account, email_config.poll_interval_sec)

    extraction_interval = int(os.environ.get("EXTRACTION_INTERVAL_SEC", "3600"))

    logger.info("Starting %d channel(s): %s", len(channels), ", ".join(channels.keys()))

    # Start all channels, the router, and the agent worker
    async with asyncio.TaskGroup() as tg:
        for channel in channels.values():
            tg.create_task(channel.start())
        tg.create_task(route_outbound(out_queue, channels))
        tg.create_task(agent_worker(agent, in_queue, out_queue))
        tg.create_task(periodic_extraction(agent, extraction_interval))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        pass
