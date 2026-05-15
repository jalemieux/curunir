"""Email channel — polls deadsimple.email for new messages, queues IncomingMessage,
sends replies into the same thread."""

import asyncio
import logging
from datetime import datetime, timezone

_RE_PREFIX_RE = ("re:", "fw:", "fwd:")
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
        """Walk pages newest-first until we hit the watermark, process new inbound."""
        cursor: str | None = None
        new_messages: list[dict[str, Any]] = []
        max_seen: tuple[datetime, str] | None = None

        while True:
            page = await self.client.list_messages(limit=50, cursor=cursor)
            data = page.get("data", [])
            stop = False
            for m in data:
                ts = self._parse_ts(m.get("created_at", ""))
                if ts is None:
                    continue
                mid = m.get("message_id", "")
                if not self.state.is_after_watermark(ts, mid):
                    stop = True
                    break
                if max_seen is None or (ts, mid) > max_seen:
                    max_seen = (ts, mid)
                new_messages.append(m)
            if stop or not page.get("next_cursor"):
                break
            cursor = page["next_cursor"]

        # Oldest first into the queue.
        for summary in reversed(new_messages):
            await self._handle_summary(summary)

        if max_seen is not None:
            self.state.set_watermark(*max_seen)
            self.state.save()

    async def _handle_summary(self, summary: dict[str, Any]) -> None:
        if summary.get("direction") != "inbound":
            return
        if summary.get("is_spam") or float(summary.get("spam_score") or 0) >= self.spam_score_threshold:
            logger.info("Dropping spam message %s (score=%s)",
                         summary.get("message_id"), summary.get("spam_score"))
            return
        sender = summary.get("from_email", "")
        if self.allowed_senders and not any(a in sender for a in self.allowed_senders):
            logger.info("Skipping email from %s (not in allowed_senders)", sender)
            return

        try:
            detail = await self.client.get_message(summary["message_id"])
        except DeadsimpleError:
            logger.exception("Failed to fetch detail for %s", summary.get("message_id"))
            return

        body = detail.get("text_body") or self._strip_html(detail.get("html_body", "")) or ""
        content = f"[channel: email, from: {sender}]\n{body}" if sender else body

        subject = detail.get("subject", "") or ""
        reply_subject = subject if subject.lower().startswith(_RE_PREFIX_RE) else f"Re: {subject}"

        incoming = IncomingMessage(
            content=content,
            channel="email",
            session_id=detail.get("thread_id", ""),
            reply_address={
                "to": sender,
                "subject": reply_subject,
                "in_reply_to": detail["message_id"],
            },
            attachments=None,  # Task 9 fills this in.
        )
        await self.in_queue.put(incoming)
        logger.info("Queued email from %s (thread %s): %s",
                    sender, incoming.session_id, subject)

    @staticmethod
    def _parse_ts(s: str) -> datetime | None:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _strip_html(html: str) -> str:
        """Crude HTML-to-text fallback used only when text_body is empty."""
        import re as _re
        return _re.sub(r"<[^>]+>", "", html).strip()

    async def send(self, msg: OutgoingMessage) -> None:
        """Stubbed until Task 10."""
        return
