from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class IncomingMessage:
    content: str
    channel: str
    session_id: str
    reply_address: dict
    command: str | None = None


@dataclass
class OutgoingMessage:
    content: str
    channel: str
    session_id: str
    reply_address: dict
    tool_calls: list[str] | None = None
    final: bool = True


class Channel(Protocol):
    async def start(self) -> None:
        """Run the channel's input loop."""
        ...

    async def send(self, msg: OutgoingMessage) -> None:
        """Receive an outbound message for delivery."""
        ...
