"""Email channel — polls deadsimple.email for new messages, queues IncomingMessage,
sends replies into the same thread."""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.channels._attachments import (
    _normalize_unicode_whitespace,
    _validate_attachment_metadata,
)
from src.channels._email_state import EmailState
from src.channels.base import IncomingMessage, OutgoingMessage
from src.channels.deadsimple import DeadsimpleClient, DeadsimpleError
from src.config import EmailChannelConfig

logger = logging.getLogger(__name__)


class EmailChannel:
    def __init__(self, in_queue: asyncio.Queue, config: EmailChannelConfig):
        self.in_queue = in_queue
        self.config = config
        self.client = DeadsimpleClient(
            api_key=config.api_key,
            api_base=config.api_base,
            inbox_id=config.inbox_id,
            allowed_recipients=config.allowed_senders,
            restrict_outbound=config.restrict_outbound,
        )
        self.poll_interval = config.poll_interval_sec
        self.allowed_senders = config.allowed_senders
        self.attachment_dir = config.attachment_dir
        self.spam_score_threshold = config.spam_score_threshold
        self.state = EmailState.load(config.state_file)

    async def start(self) -> None:
        """Validate inbox, initialize watermark if needed, enter polling loop."""
        try:
            inbox = await self.client.validate_inbox()
        except DeadsimpleError as e:
            logger.error("Email channel failed to start (invalid inbox): %s", e)
            return

        email_addr = (inbox.get("data") or inbox).get("email", "<unknown>")
        logger.info("Email channel started, inbox=%s, polling every %ds",
                    email_addr, self.poll_interval)

        if self.state.watermark_created_at is None:
            self.state.set_watermark(datetime.now(timezone.utc), "")
            self.state.save()

        await self._poll_loop()

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._poll_once()
            except Exception:
                logger.exception("Error during email poll")
            await asyncio.sleep(self.poll_interval)

    async def _poll_once(self) -> None:
        """Stubbed until Task 8."""
        return

    async def send(self, msg: OutgoingMessage) -> None:
        """Stubbed until Task 10."""
        return
