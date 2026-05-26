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

_REPLY_PREFIXES = ("re:", "fw:", "fwd:")


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
        """Walk pages until we hit a page entirely ≤ watermark, process new inbound.

        The deadsimple list endpoint does not guarantee strict newest-first
        ordering — the watermark message itself can appear at index 0 with
        newer messages further down. So sort each page locally by
        (created_at, message_id) descending and only terminate pagination
        when the entire sorted page is ≤ watermark (a within-page miss is
        not enough).

        Outbound messages count toward pagination (page_had_new) but never
        advance the watermark. Otherwise a scheduled outbound at T+1 would
        push the watermark past an inbound at T whose listing was delayed,
        silently dropping it on every subsequent poll.
        """
        cursor: str | None = None
        new_messages: list[dict[str, Any]] = []
        max_seen: tuple[datetime, str] | None = None

        while True:
            page = await self.client.list_messages(limit=50, cursor=cursor)
            data = page["messages"]
            keyed: list[tuple[datetime, str, dict[str, Any]]] = []
            for m in data:
                ts = self._parse_ts(m.get("created_at", ""))
                if ts is None:
                    continue
                keyed.append((ts, m.get("message_id", ""), m))
            keyed.sort(key=lambda x: (x[0], x[1]), reverse=True)

            page_had_new = False
            for ts, mid, m in keyed:
                if not self.state.is_after_watermark(ts, mid):
                    continue
                page_had_new = True
                if m.get("direction") != "inbound":
                    continue
                if max_seen is None or (ts, mid) > max_seen:
                    max_seen = (ts, mid)
                new_messages.append(m)
            if not page.get("next_cursor") or not page_had_new:
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

        thread_id = detail.get("thread_id", "")
        attachments = await self._process_attachments(detail, thread_id)

        body = detail.get("text_body") or self._strip_html(detail.get("html_body", "")) or ""
        content = f"[channel: email, from: {sender}]\n{body}" if sender else body
        if attachments:
            content += "\n\nAttachments:\n"
            for att in attachments:
                size_kb = max(att["size"] // 1024, 1)
                content += f"- {att['filename']} ({att['mime_type']}, {size_kb}KB) -> {att['path']}\n"

        subject = detail.get("subject", "") or ""
        reply_subject = subject if subject.lower().startswith(_REPLY_PREFIXES) else f"Re: {subject}"

        incoming = IncomingMessage(
            content=content,
            channel="email",
            session_id=thread_id,
            reply_address={
                "to": sender,
                "subject": reply_subject,
                "in_reply_to": detail["message_id"],
            },
            attachments=attachments,
        )
        await self.in_queue.put(incoming)
        logger.info("Queued email from %s (thread %s): %s",
                    sender, incoming.session_id, subject)

    async def _process_attachments(
        self, detail: dict[str, Any], thread_id: str
    ) -> list[dict] | None:
        raw = detail.get("attachments") or []
        if not raw:
            return None
        out_dir = Path(self.attachment_dir).resolve() / thread_id
        out_dir.mkdir(parents=True, exist_ok=True)

        manifest: list[dict] = []
        for att in raw:
            att_id = att.get("attachment_id")
            fname_raw = att.get("filename", "")
            if not att_id or not fname_raw:
                continue
            fname = _normalize_unicode_whitespace(fname_raw)
            mime = att.get("content_type") or "application/octet-stream"
            declared_size = int(att.get("size") or 0)
            reason = _validate_attachment_metadata(mime, declared_size)
            if reason:
                logger.warning("Dropping email attachment %s: %s", fname, reason)
                continue
            dest = out_dir / fname
            try:
                await self.client.download_attachment(detail["message_id"], att_id, dest)
            except DeadsimpleError:
                logger.exception("Failed to download attachment %s", fname)
                continue
            if not dest.is_file():
                continue
            manifest.append({
                "filename": fname,
                "path": str(dest),
                "mime_type": mime,
                "size": dest.stat().st_size,
            })
        return manifest or None

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
        """Send a reply via deadsimple. Routes to /reply for text-only, /messages when attaching."""
        if not msg.final or not msg.content:
            return
        in_reply_to = msg.reply_address.get("in_reply_to")
        to = msg.reply_address.get("to")
        subject = msg.reply_address.get("subject")
        if not in_reply_to or not to:
            logger.error("Email send missing in_reply_to or to (got %s)", msg.reply_address)
            return

        attachments = msg.attachments or []
        try:
            if attachments:
                paths = [a["path"] for a in attachments if a.get("path")]
                await self.client.send_with_attachments(
                    in_reply_to=in_reply_to,
                    to=to,
                    subject=subject or "",
                    text_body=msg.content,
                    attachment_paths=paths,
                )
            else:
                await self.client.send_reply(
                    in_reply_to=in_reply_to,
                    to=to,
                    text_body=msg.content,
                )
        except DeadsimpleError:
            logger.exception("Failed to send reply for thread %s", msg.session_id)
