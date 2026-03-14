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
        logger.info("gog CLI verified")
        await self._ensure_label()
        logger.info("Email channel started, polling every %ds", self.poll_interval)
        await self._poll_loop()

    async def send(self, msg: OutgoingMessage) -> None:
        """Send a reply in the original thread."""
        if not msg.content:
            return
        logger.info("Sending reply to %s (thread %s)", msg.reply_address.get("to"), msg.session_id)
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
        query = f"in:inbox -label:{self.processed_label}"
        threads = await asyncio.to_thread(gog.search, query, self.account)
        new_threads = [t for t in threads if t["id"] not in self.last_seen]
        if new_threads:
            logger.info("Found %d new thread(s) (%d total unprocessed)", len(new_threads), len(threads))

        for thread_summary in new_threads:
            thread_id = thread_summary["id"]

            try:
                thread = await asyncio.to_thread(gog.thread_get, thread_id, self.account)
            except gog.GogError:
                logger.exception("Failed to fetch thread %s", thread_id)
                continue

            # Label immediately so we don't re-process on next poll
            try:
                await asyncio.to_thread(
                    gog.thread_modify, thread_id,
                    add_label=self.processed_label, account=self.account,
                )
            except gog.GogError:
                logger.exception("Failed to label thread %s on ingest", thread_id)

            messages = thread.get("messages", [])
            new_messages = self._new_messages(messages, self.last_seen.get(thread_id))

            # Mark thread as seen up to latest message, even if none are queued
            if messages:
                self.last_seen[thread_id] = messages[-1]["id"]

            for message in new_messages:

                sender = message.get("from", "")
                if self.allowed_senders and not any(
                    allowed in sender for allowed in self.allowed_senders
                ):
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
                logger.info("Queued email from %s (thread %s): %s", sender, thread_id, subject)

    @staticmethod
    def _new_messages(messages: list[dict], last_seen_id: str | None) -> list[dict]:
        """Return messages after last_seen_id, or only the latest if not seen before."""
        if last_seen_id is None:
            # First encounter — only process the most recent message
            return messages[-1:] if messages else []

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

        out_dir = os.path.join(os.path.abspath(self.attachment_dir), thread_id)

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
