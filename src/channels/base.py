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
    # True when the originating client is voice-only (e.g. the iOS PTT app);
    # agent_worker synthesizes the final reply as speech for these turns.
    voice: bool = False


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


class Channel(Protocol):
    async def start(self) -> None:
        """Run the channel's input loop."""
        ...

    async def send(self, msg: OutgoingMessage) -> None:
        """Receive an outbound message for delivery."""
        ...
