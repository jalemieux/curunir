"""Email channel — polls Gmail via gog CLI, processes inbound messages, sends replies."""

import asyncio
import logging
import os

from src.channels import gog
from src.channels.base import IncomingMessage, OutgoingMessage
from src.config import EmailChannelConfig

logger = logging.getLogger(__name__)


class EmailChannel:
    def __init__(self, in_queue: asyncio.Queue, config: EmailChannelConfig):
        self.in_queue = in_queue
        self.config = config
        self.account = config.account
        self.poll_interval = config.poll_interval_sec
        self.allowed_senders = config.allowed_senders
        self.processed_label = config.processed_label
        self.attachment_dir = config.attachment_dir
        self.last_seen: dict[str, str] = {}

    async def _ensure_label(self) -> None:
        """Ensure the processed label exists in Gmail, creating it if missing."""
        labels = await asyncio.to_thread(gog.labels_list, self.account)
        if not any(label.get("name") == self.processed_label for label in labels):
            await asyncio.to_thread(gog.labels_create, self.processed_label, self.account)

    async def start(self) -> None:
        """Verify gog, bootstrap label, enter polling loop."""
        await asyncio.to_thread(gog.check_installed)
        await self._ensure_label()
        await self._poll_loop()

    async def send(self, msg: OutgoingMessage) -> None:
        """Send a reply and label the thread as processed."""
        pass  # implemented in Task 6

    async def _poll_loop(self) -> None:
        """Poll for new messages on an interval."""
        pass  # implemented in Task 5
