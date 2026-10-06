from dataclasses import dataclass, field
from typing import Protocol


# attachments: list of {"filename": str, "path": str, "mime_type": str, "size": int}
#   — produced by ws.py (CLI uploads) and email.py (email attachments), same shape.
@dataclass
class IncomingMessage:
    content: str
    channel: str
    session_id: str
    reply_address: dict
    command: str | None = None
    attachments: list[dict] | None = None
    # Which agent in the container should handle this (None = the default
    # agent, which is every pre-multi-agent client). route_inbound resolves
    # it and stamps the canonical name before the worker sees the message.
    agent: str | None = None


@dataclass
class OutgoingMessage:
    content: str
    channel: str
    session_id: str
    reply_address: dict
    tool_calls: list[str] | None = None
    final: bool = True
    delta: bool = False
    attachments: list[dict] | None = None
    workflow: dict | None = None
    stats: dict | None = None
    # The agent that produced this reply; channels echo it on outbound frames.
    agent: str | None = None
    # Sibling handoffs made during this turn, as ``{agent, session_id}``: the
    # local console offers "continue with <agent>" for each. Other channels
    # ignore it.
    handoffs: list[dict] | None = None
    # Which stretch of the turn's text this frame belongs to (see
    # ``TextSegments`` in run.py). Set on text deltas and on the final reply;
    # a client that ignores it behaves as before.
    segment: int | None = None


class Channel(Protocol):
    async def start(self) -> None:
        """Run the channel's input loop."""
        ...

    async def send(self, msg: OutgoingMessage) -> None:
        """Receive an outbound message for delivery."""
        ...
