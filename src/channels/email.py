"""Email channel — polls Gmail via gog CLI, processes inbound messages, sends replies."""

import asyncio
import logging
import os
import re

from src.channels import gog
from src.channels.base import IncomingMessage, OutgoingMessage
from src.config import EmailChannelConfig

logger = logging.getLogger(__name__)

# Regex matching any Unicode whitespace character that isn't a regular space
_UNICODE_WHITESPACE_RE = re.compile(r'[^\S ]+')


def _normalize_unicode_whitespace(s: str) -> str:
    """Replace Unicode whitespace characters (e.g. \\u202f) with regular spaces."""
    return _UNICODE_WHITESPACE_RE.sub(' ', s)


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
        """Send a reply in the original thread, with optional attachments."""
        if not msg.content:
            return
        attachment_paths = [att["path"] for att in msg.attachments] if msg.attachments else None
        logger.info("Sending reply to %s (thread %s, %d attachment(s))",
                     msg.reply_address.get("to"), msg.session_id,
                     len(attachment_paths) if attachment_paths else 0)
        try:
            await asyncio.to_thread(
                gog.send_reply,
                to=msg.reply_address["to"],
                subject=msg.reply_address["subject"],
                body=msg.content,
                reply_to_message_id=msg.reply_address["in_reply_to"],
                account=self.account,
                attachments=attachment_paths,
            )
        except gog.GogError:
            logger.exception("Failed to send reply for thread %s", msg.session_id)
            return
        try:
            await asyncio.to_thread(
                gog.thread_modify, msg.session_id,
                add_label=self.processed_label, account=self.account,
            )
        except gog.GogError:
            logger.exception("Failed to label thread %s after send", msg.session_id)

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

        for thread_summary in threads:
            thread_id = thread_summary["id"]

            try:
                thread = await asyncio.to_thread(gog.thread_get, thread_id, self.account)
            except gog.GogError:
                logger.exception("Failed to fetch thread %s", thread_id)
                continue

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
                    logger.info("Skipping email from %s (not in allowed_senders)", sender)
                    continue

                subject = message.get("subject", "")
                reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"

                attachments = await self._process_attachments(thread_id, message)
                logger.info("Attachments for msg %s: %s",
                            message["id"],
                            [a["path"] for a in attachments] if attachments else None)

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
        os.makedirs(out_dir, exist_ok=True)

        try:
            await asyncio.to_thread(gog.thread_download_attachments, thread_id, out_dir, self.account)
        except gog.GogError:
            logger.exception("Failed to download attachments for thread %s", thread_id)
            return None

        # Rename downloaded files to normalize Unicode whitespace (e.g. \u202f)
        # to regular spaces. LLMs convert exotic whitespace to regular spaces
        # when generating tool calls, causing file-not-found errors.
        self._normalize_filenames(out_dir)

        # gog saves files with an attachment-ID prefix (e.g. "19d00de1_ANGj_orig.png").
        # Match downloaded files to expected attachments by suffix.
        downloaded = os.listdir(out_dir)
        logger.info("Attachment dir %s: %d file(s) %s", out_dir, len(downloaded), downloaded)

        manifest = []
        for att in raw_attachments:
            fname = _normalize_unicode_whitespace(att["filename"])
            # Try exact match first, then suffix match for prefixed filenames
            actual = fname if fname in downloaded else next(
                (f for f in downloaded if f.endswith("_" + fname)), None
            )
            if actual is None:
                logger.warning("Attachment not found on disk: %s (dir contains: %s)", fname, downloaded)
                continue
            full_path = os.path.join(out_dir, actual)
            if not os.path.isfile(full_path):
                logger.warning("Attachment matched but not a file: %s", full_path)
                continue
            manifest.append({
                "filename": fname,
                "path": full_path,
                "mime_type": att.get("mimeType", "application/octet-stream"),
                "size": att.get("size", 0),
            })
        return manifest or None

    @staticmethod
    def _normalize_filenames(directory: str) -> None:
        """Rename files to replace Unicode whitespace with regular spaces."""
        for name in os.listdir(directory):
            normalized = _normalize_unicode_whitespace(name)
            if normalized != name:
                old = os.path.join(directory, name)
                new = os.path.join(directory, normalized)
                os.rename(old, new)
                logger.info("Renamed attachment: %r -> %r", name, normalized)
