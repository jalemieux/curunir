"""Run the CLI channel with the real agent loop.

Usage:
    python run_cli.py
"""

import asyncio
import json

from dotenv import load_dotenv

from src.agent.agent import Agent
from src.channels.base import OutgoingMessage
from src.channels.cli import CLIChannel
from src.channels.router import route_outbound
from src.config import AgentConfig


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
        case _:
            return f"{name} {args_str}"


async def agent_worker(agent: Agent, in_queue: asyncio.Queue, out_queue: asyncio.Queue):
    """Bridge between the message queues and the agent loop."""
    while True:
        msg = await in_queue.get()

        if msg.command == "clear":
            agent.sessions.pop(msg.session_id, None)
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

        text = await agent.handle(msg.content, msg.session_id, on_tool_call=on_tool_call)

        await out_queue.put(OutgoingMessage(
            content=text,
            channel=msg.channel,
            session_id=msg.session_id,
            reply_address=msg.reply_address,
        ))


async def main():
    load_dotenv()
    config = AgentConfig()

    agent = Agent(config)
    in_queue = asyncio.Queue()
    out_queue = asyncio.Queue()

    cli = CLIChannel(in_queue, model=config.model)
    channels = {"cli": cli}

    async with asyncio.TaskGroup() as tg:
        tg.create_task(cli.start())
        tg.create_task(route_outbound(out_queue, channels))
        tg.create_task(agent_worker(agent, in_queue, out_queue))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        pass
