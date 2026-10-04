# src/turn_context.py
"""Where the current user turn came from.

``agent_worker`` (``run.py``) sets :data:`current_turn` before it calls
``Agent.handle`` for a user message. Tool executors run inside that task (or
in tasks ``asyncio.gather`` copies its context into), so a tool that needs
the turn's channel and reply address reads it here instead of every tool
signature growing those arguments. Only ``handoff`` does today: a sibling
handoff opens the receiver's conversation on the channel the user is on.

Unset (``None``) for anything that is not a user turn: scheduled tasks,
``delegate`` / ``ask_agent`` sub-agents run from another event-loop entry
point, and direct ``Agent.handle`` calls in tests.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class TurnContext:
    channel: str
    session_id: str
    reply_address: dict
    # Sibling handoffs made during this turn, as ``{agent, session_id}``.
    # ``agent_worker`` puts them on the final reply so a console can offer
    # "continue with <agent>".
    handoffs: list[dict] = field(default_factory=list)


current_turn: ContextVar[TurnContext | None] = ContextVar("current_turn", default=None)
