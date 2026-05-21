"""Slash command dispatcher.

Two layers:

1. Intercepted registry (`INTERCEPTED`) — explicit handlers for utility
   commands that don't need the LLM (`/help`, `/skills`, `/clear`, plus
   `/new` and `/reset` aliases). Each handler returns a `SlashResult`
   describing the outgoing replies and/or `IncomingMessage`s the caller
   should enqueue.
2. Skill-forcing fallback — anything not intercepted is looked up against
   the live skill registry. A match rewrites the text into a synthetic
   user prompt that directs the agent to call the `load_skill` tool with the
   exact name and enqueues it; a miss returns a polite "unknown command"
   message. The prompt is deliberately imperative and notes that the skill
   may be absent from the system-prompt "Available Skills" catalog (hidden
   skills are), so the agent loads it instead of assuming it doesn't exist.

`maybe_handle_slash()` is the single entry point. It is called from
`agent_worker` after a `command="slash"` message is dequeued — channels
are dumb about slash and just forward the raw text. Cancellation is
handled separately as an out-of-band `interrupt` frame at the channel
layer (the agent_worker is blocked inside `handle()` during a turn, so a
queue-based `/cancel` would arrive too late to matter).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from src.channels.base import IncomingMessage, OutgoingMessage
from src.skills import load_registry

logger = logging.getLogger(__name__)


@dataclass
class SlashContext:
    args: str
    session_id: str
    channel: str
    reply_address: dict
    skill_dirs: list[Path]


@dataclass
class SlashResult:
    handled: bool
    outgoing: list[OutgoingMessage] = field(default_factory=list)
    enqueue: list[IncomingMessage] = field(default_factory=list)


# One-liner help strings keyed by command name. Aliases share an entry; the
# help table de-duplicates by description.
_HELP: dict[str, str] = {
    "help": "show this help",
    "skills": "list available skills",
    "clear": "wipe conversation history (aliases: /new, /reset)",
}

# Footer for /help: how to stop a turn in flight. Slash can't do this — the
# agent_worker is blocked while a turn runs, so a queued /cancel would arrive
# too late. Cancellation lives in the channel as an out-of-band frame.
_CANCEL_HINT = (
    "To stop the agent mid-turn, press Ctrl-C in the CLI or use the stop "
    "button in the portal."
)


def _out(ctx: SlashContext, content: str) -> OutgoingMessage:
    return OutgoingMessage(
        content=content,
        channel=ctx.channel,
        session_id=ctx.session_id,
        reply_address=ctx.reply_address,
        final=True,
    )


def _inc(ctx: SlashContext, content: str, command: str | None = None) -> IncomingMessage:
    return IncomingMessage(
        content=content,
        channel=ctx.channel,
        session_id=ctx.session_id,
        reply_address=ctx.reply_address,
        command=command,
    )


async def _help(ctx: SlashContext) -> SlashResult:
    lines = [
        "## Slash commands",
        "",
        "| Command | What it does |",
        "|---------|--------------|",
    ]
    for name in ("help", "skills", "clear"):
        lines.append(f"| `/{name}` | {_HELP[name]} |")

    registry = load_registry(ctx.skill_dirs)
    if registry:
        lines.append("")
        lines.append("## Skill shortcuts")
        lines.append("")
        lines.append("Type `/<skill-name> [args]` to force the agent to use that skill.")
        lines.append("")
        lines.append("| Skill | When to use |")
        lines.append("|-------|-------------|")
        for skill in sorted(registry.values(), key=lambda s: s.name):
            lines.append(f"| `/{skill.name}` | {skill.description} |")

    lines.append("")
    lines.append(_CANCEL_HINT)

    return SlashResult(handled=True, outgoing=[_out(ctx, "\n".join(lines))])


async def _skills(ctx: SlashContext) -> SlashResult:
    registry = load_registry(ctx.skill_dirs)
    if not registry:
        return SlashResult(handled=True, outgoing=[_out(ctx, "No skills registered.")])
    lines = [
        "## Available skills",
        "",
        "| Skill | When to use |",
        "|-------|-------------|",
    ]
    for skill in sorted(registry.values(), key=lambda s: s.name):
        lines.append(f"| `/{skill.name}` | {skill.description} |")
    return SlashResult(handled=True, outgoing=[_out(ctx, "\n".join(lines))])


async def _clear(ctx: SlashContext) -> SlashResult:
    return SlashResult(handled=True, enqueue=[_inc(ctx, "", command="clear")])


INTERCEPTED: dict[str, Callable[[SlashContext], Awaitable[SlashResult]]] = {
    "help": _help,
    "skills": _skills,
    "clear": _clear,
    "new": _clear,
    "reset": _clear,
}


async def maybe_handle_slash(
    text: str,
    attachments: list[dict] | None,
    ctx: SlashContext,
) -> SlashResult | None:
    """Dispatch `text` if it looks like a slash command.

    Returns:
        - `None`: not a slash command (or attachments present). Caller should
          treat the message as a normal user turn.
        - `SlashResult`: caller should send each `outgoing` message and put
          each `enqueue` message on the in-queue.
    """
    if not text or not text.startswith("/"):
        return None
    if attachments:
        # v1: slash commands don't accept attachments. Fall through so the
        # caller treats the literal text as a normal user turn.
        logger.info("Slash command with attachments — falling through to agent")
        return None

    body = text[1:].lstrip()
    if not body:
        return None
    parts = body.split(None, 1)
    name = parts[0]
    args = parts[1] if len(parts) > 1 else ""

    ctx_with_args = SlashContext(
        args=args,
        session_id=ctx.session_id,
        channel=ctx.channel,
        reply_address=ctx.reply_address,
        skill_dirs=ctx.skill_dirs,
    )

    handler = INTERCEPTED.get(name)
    if handler is not None:
        return await handler(ctx_with_args)

    registry = load_registry(ctx.skill_dirs)
    if name in registry:
        prompt = (
            f'Call the `load_skill` tool with name="{name}" to load that '
            f"skill, then follow its instructions. The skill may not appear "
            f'in your "Available Skills" list, but it exists and is loadable '
            f"by its exact name."
        )
        if args:
            prompt = f"{prompt} {args}"
        return SlashResult(handled=True, enqueue=[_inc(ctx_with_args, prompt)])

    return SlashResult(
        handled=True,
        outgoing=[_out(
            ctx_with_args,
            f"Unknown command `/{name}`. Try `/help` to see what's available.",
        )],
    )
