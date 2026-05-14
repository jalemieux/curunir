"""Slash command dispatcher.

Two layers:

1. Intercepted registry (`INTERCEPTED`) — explicit handlers for utility
   commands that don't need the LLM (`/help`, `/skills`, `/clear`, `/cancel`,
   plus `/new` and `/reset` aliases). Each handler returns a `SlashResult`
   describing the outgoing replies and/or `IncomingMessage`s the channel
   should enqueue.
2. Skill-forcing fallback — anything not intercepted is looked up against
   the live skill registry. A match rewrites the text into a synthetic
   user prompt (`"Use the `<name>` skill. {args}"`) and enqueues it; a miss
   returns a polite "unknown command" message.

`maybe_handle_slash()` is the single entry point. Channels call it BEFORE
enqueuing user input. Email is excluded — slash semantics don't fit threaded
mail and the false-positive risk is high.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from src.channels.base import IncomingMessage, OutgoingMessage
from src.skills import load_registry

logger = logging.getLogger(__name__)


class _CancelCapable(Protocol):
    def request_cancel(self, session_id: str) -> bool: ...


@dataclass
class SlashContext:
    args: str
    session_id: str
    channel: str
    reply_address: dict
    agent: _CancelCapable
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
    "cancel": "stop the agent's current turn",
}


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
    for name in ("help", "skills", "clear", "cancel"):
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


async def _cancel(ctx: SlashContext) -> SlashResult:
    delivered = bool(ctx.agent and ctx.agent.request_cancel(ctx.session_id))
    if delivered:
        msg = "Interrupt requested — finishing current step, then stopping."
    else:
        msg = "No agent run in progress to cancel."
    return SlashResult(handled=True, outgoing=[_out(ctx, msg)])


INTERCEPTED: dict[str, Callable[[SlashContext], Awaitable[SlashResult]]] = {
    "help": _help,
    "skills": _skills,
    "clear": _clear,
    "new": _clear,
    "reset": _clear,
    "cancel": _cancel,
}


async def maybe_handle_slash(
    text: str,
    attachments: list[dict] | None,
    ctx: SlashContext,
) -> SlashResult | None:
    """Dispatch `text` if it looks like a slash command.

    Returns:
        - `None`: not a slash command (or attachments present). Channel should
          proceed with normal enqueue.
        - `SlashResult`: the channel should send each `outgoing` message and
          put each `enqueue` message on the in-queue.
    """
    if not text or not text.startswith("/"):
        return None
    if attachments:
        # v1: slash commands don't accept attachments. Fall through so the
        # channel enqueues the literal text and the agent can see it.
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
        agent=ctx.agent,
        skill_dirs=ctx.skill_dirs,
    )

    handler = INTERCEPTED.get(name)
    if handler is not None:
        return await handler(ctx_with_args)

    registry = load_registry(ctx.skill_dirs)
    if name in registry:
        prompt = f"Use the `{name}` skill."
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
