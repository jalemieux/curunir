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
        """Send a reply in the original thread and label it as processed."""
        try:
            await asyncio.to_thread(
                gog.send_reply,
                to=msg.reply_address["to"],
                subject=msg.reply_address["subject"],
                body=msg.content,
                reply_to_message_id=msg.reply_address["in_reply_to"],
                account=self.account,
            )
        except gog.GogError:
            logger.exception("Failed to send reply for thread %s", msg.session_id)
            return

        try:
            await asyncio.to_thread(
                gog.thread_modify,
                msg.session_id,
                add_label=self.processed_label,
                account=self.account,
            )
        except gog.GogError:
            logger.exception("Failed to label thread %s as processed", msg.session_id)

    async def _poll_loop(self) -> None:
        """Poll for new messages on an interval."""
        while True:
            try:
                await self._poll_once()
            except Exception:
                logger.exception("Error during email poll")
            await asyncio.sleep(self.poll_interval)

    async def _poll_once(self) -> None:
        """Run one poll cycle: search for unprocessed threads and process new messages."""
        query = f"-label:{self.processed_label}"
        threads = await asyncio.to_thread(gog.search, query, self.account)

        for thread_summary in threads:
            thread_id = thread_summary["id"]
            try:
                thread = await asyncio.to_thread(gog.thread_get, thread_id, self.account)
            except gog.GogError:
                logger.exception("Failed to fetch thread %s", thread_id)
                continue

            messages = thread.get("messages", [])
            last_seen_id = self.last_seen.get(thread_id)

            new_messages = self._new_messages(messages, last_seen_id)

            for message in new_messages:
                self.last_seen[thread_id] = message["id"]

                sender = message.get("from", "")
                if self.allowed_senders and sender not in self.allowed_senders:
                    continue

                subject = message.get("subject", "")
                reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"

                attachments = await self._process_attachments(thread_id, message)

                content = message.get("body", "")
                if attachments:
                    content += "\n\nAttachments:\n"
                    for att in attachments:
                        size_kb = att["size"] // 1024
                        content += f"- {att['filename']} ({att['mime_type']}, {size_kb}KB) -> {att['path']}\n"

                incoming = IncomingMessage(
                    content=content,
                    channel="email",
                    session_id=thread_id,
                    reply_address={
                        "to": sender,
                        "subject": reply_subject,
                        "in_reply_to": message["id"],
                    },
                    attachments=attachments if attachments else None,
                )
                await self.in_queue.put(incoming)

    @staticmethod
    def _new_messages(messages: list[dict], last_seen_id: str | None) -> list[dict]:
        """Return messages after last_seen_id, or all if not seen before."""
        if last_seen_id is None:
            return messages

        found = False
        new = []
        for msg in messages:
            if found:
                new.append(msg)
            elif msg["id"] == last_seen_id:
                found = True
        return new

    async def _process_attachments(self, thread_id: str, message: dict) -> list[dict] | None:
        """Download and build attachment manifest if message has attachments."""
        raw_attachments = message.get("attachments", [])
        if not raw_attachments:
            return None

        out_dir = os.path.join(self.attachment_dir, thread_id)

        try:
            await asyncio.to_thread(gog.thread_download_attachments, thread_id, out_dir, self.account)
        except gog.GogError:
            logger.exception("Failed to download attachments for thread %s", thread_id)
            return None

        manifest = []
        for att in raw_attachments:
            manifest.append({
                "filename": att["filename"],
                "path": os.path.join(out_dir, att["filename"]),
                "mime_type": att.get("mimeType", "application/octet-stream"),
                "size": att.get("size", 0),
            })
        return manifest
