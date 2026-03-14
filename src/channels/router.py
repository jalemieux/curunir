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
        await channel.send(msg)
