from dataclasses import dataclass, field
from typing import Protocol

# The tenant dimension carried end-to-end through the queues. A blank persona
# means "the default persona"; the agent_worker dispatcher resolves it against
# the registry (and falls back to the single configured runtime), so legacy
# channels that never set it keep working.
DEFAULT_PERSONA = "default"


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
    persona: str = DEFAULT_PERSONA


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
    persona: str = DEFAULT_PERSONA


class Channel(Protocol):
    async def start(self) -> None:
        """Run the channel's input loop."""
        ...

    async def send(self, msg: OutgoingMessage) -> None:
        """Receive an outbound message for delivery."""
        ...
