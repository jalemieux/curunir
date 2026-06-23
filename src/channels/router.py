import asyncio
import logging

from src.channels.base import Channel, OutgoingMessage

logger = logging.getLogger(__name__)


async def route_outbound(out_queue: asyncio.Queue, channels: dict[str, Channel]):
    while True:
        msg: OutgoingMessage = await out_queue.get()
        # A channel that fans out per persona (e.g. one Fastmail inbox each) is
        # registered as "<channel>:<persona>"; a single multiplexed channel is
        # registered under its bare name. Try the persona-qualified key first,
        # then fall back to the bare name so legacy single-instance channels
        # (cli, portal, local_web) keep matching.
        channel = channels.get(f"{msg.channel}:{msg.persona}") or channels.get(msg.channel)
        if channel is None:
            logger.warning(
                "No channel registered for %r (persona %r), discarding message",
                msg.channel, msg.persona,
            )
            continue
        await channel.send(msg)
