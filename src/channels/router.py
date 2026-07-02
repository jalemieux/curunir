import asyncio
import logging

from src.channels.base import Channel, OutgoingMessage

logger = logging.getLogger(__name__)


async def route_outbound(out_queue: asyncio.Queue, channels: dict[str, Channel]):
    while True:
        msg: OutgoingMessage = await out_queue.get()
        channel = channels.get(msg.channel)
        if channel is None:
            logger.warning("No channel registered for %r, discarding message", msg.channel)
            continue
        try:
            await channel.send(msg)
        except Exception:
            # A failed outbound delivery (WS client gone mid-write, dropped
            # portal socket, local-web bridge error) must degrade to a
            # dropped message + logged error — an exception escaping the
            # router cancels the TaskGroup and takes every channel down with
            # it. `except Exception` (not BaseException) lets CancelledError
            # through so TaskGroup shutdown still cancels the router cleanly.
            logger.exception(
                "Failed to deliver message to channel %r (session %s), dropping",
                msg.channel, msg.session_id,
            )
