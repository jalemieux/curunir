"""Email channel — polls a Fastmail INBOX (IMAP) for new messages, queues
IncomingMessage, sends replies into the same thread (SMTP).

The transport lives in `FastmailClient`; this channel and its discovery
cursor / pending-reply ledger are transport-agnostic above the client
boundary (they key on the RFC822 Message-ID and Date)."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
from pathlib import Path
from typing import Any, Callable

from src.channels._attachments import (
    _assert_within,
    _safe_attachment_filename,
    _validate_attachment_metadata,
)
from src.channels._email_state import EmailState
from src.channels.base import IncomingMessage, OutgoingMessage
from src.channels.fastmail import FastmailClient, FastmailError
from src.config import EmailChannelConfig

logger = logging.getLogger(__name__)

_REPLY_PREFIXES = ("re:", "fw:", "fwd:")

_HTML_WRAPPER = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  body {{ font-family: -apple-system, system-ui, "Segoe UI", sans-serif; max-width: 700px; margin: 0 auto; padding: 24px 16px; font-size: 16px; line-height: 1.6; color: #1a1a1a; }}
  h1 {{ font-size: 24px; margin: 0 0 0.8em 0; font-weight: 600; }}
  h2 {{ font-size: 20px; margin: 1.2em 0 0.6em 0; font-weight: 600; }}
  h3 {{ font-size: 17px; margin: 1.1em 0 0.5em 0; font-weight: 600; }}
  p {{ margin: 0 0 1.1em 0; }}
  strong {{ font-weight: 600; }}
  em {{ font-style: italic; }}
  a {{ color: #1a5fb4; text-decoration: underline; }}
  ul, ol {{ margin: 0 0 1.1em 0; padding-left: 1.6em; }}
  li {{ margin: 0.2em 0; }}
  code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.92em; background: #f3f3f3; padding: 0.1em 0.3em; border-radius: 3px; }}
  pre {{ background: #f3f3f3; padding: 12px; border-radius: 4px; overflow-x: auto; line-height: 1.4; }}
  pre code {{ background: none; padding: 0; }}
  blockquote {{ margin: 0 0 1.1em 0; padding: 0 1em; border-left: 3px solid #ddd; color: #555; }}
</style></head><body>
{body}
</body></html>"""


def _render_html(markdown_text: str) -> str:
    """Render markdown to a styled standalone HTML document for email delivery.

    Returns the raw markdown wrapped in a <pre> if the markdown library is
    unavailable for any reason — caller treats an empty/None return as the
    text-only fallback, but here we degrade gracefully to keep the body usable.
    """
    try:
        import markdown as _md
    except ImportError:
        logger.warning("markdown library unavailable; falling back to text-only email")
        return ""
    rendered = _md.markdown(markdown_text, extensions=["extra", "sane_lists"])
    return _HTML_WRAPPER.format(body=rendered)


class EmailChannel:
    def __init__(self, in_queue: asyncio.Queue, config: EmailChannelConfig):
        self.in_queue = in_queue
        self.config = config
        self.client = FastmailClient(
            imap_host=config.imap_host,
            smtp_host=config.smtp_host,
            user=config.user,
            password=config.password,
            inbox=config.inbox,
            allowed_recipients=config.allowed_senders,
            restrict_outbound=config.restrict_outbound,
        )
        self.poll_interval = config.poll_interval_sec
        self.allowed_senders = [s.lower() for s in config.allowed_senders]
        self.attachment_dir = config.attachment_dir
        self.spam_score_threshold = config.spam_score_threshold
        self.state = EmailState.load(config.state_file)
        # Consecutive poll/send failures; reset on any success. When it crosses
        # config.failure_alert_threshold an ERROR-level escalation fires once.
        self._consecutive_failures = 0
        # Optional operator-notification hook: callable(reason: str). The
        # baseline escalation is an ERROR log; a louder signal can be injected.
        self._escalate_hook: Callable[[str], None] | None = None

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    async def start(self) -> None:
        """Validate inbox, classify boot state, re-drive the ledger, then poll."""
        try:
            inbox = await self.client.validate_inbox()
        except FastmailError as e:
            logger.error("Email channel failed to start (invalid inbox): %s", e)
            return

        email_addr = (inbox.get("data") or inbox).get("email", "<unknown>")
        logger.info("Email channel started, inbox=%s, polling every %ds",
                    email_addr, self.poll_interval)

        # Corrupt state file: a lost/garbled watermark must NOT fast-forward past
        # pre-existing mail (that would silently drop it). Alert and bail so an
        # operator repairs or removes the file instead.
        if self.state.corrupt:
            logger.error(
                "Email state file %s is corrupt; not polling and not "
                "fast-forwarding. Repair or remove it to resume.",
                self.config.state_file,
            )
            self._escalate(
                f"email state file {self.config.state_file} is corrupt — "
                "inbound mail is not being polled"
            )
            return

        # Genuine first run (no prior cursor): skip pre-existing mail.
        if self.state.cursor_created_at is None:
            self.state.set_cursor(self._now(), "")
            self.state.save()
            logger.info("Email first run: skipping pre-existing mail (cursor=now)")

        await self._redrive_ledger()
        await self._poll_loop()

    async def _redrive_ledger(self) -> None:
        """Recover in-flight work persisted across a restart/crash.

        status=queued → the agent never replied (crashed mid-turn); re-fetch the
        inbound by message_id and re-enqueue it. status=retry → the reply was
        computed but the send failed; the drain loop re-sends the stored payload.
        """
        for mid, pr in list(self.state.pending.items()):
            if pr.status != "queued":
                continue
            try:
                detail = await self.client.get_message(mid)
            except FastmailError:
                logger.exception("Re-drive: failed to re-fetch queued message %s", mid)
                continue
            sender = pr.reply_address.get("to", "") or detail.get("from_email", "")
            await self._enqueue_detail(detail, sender)
            logger.info("Re-drive: re-enqueued queued message %s", mid)
        await self._drain_retries()

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._poll_once()
            except Exception:
                logger.exception("Error during email poll")
                self._note_failure()
            try:
                await self._drain_retries()
            except Exception:
                logger.exception("Error during email retry drain")
            await asyncio.sleep(self.poll_interval)

    async def _poll_once(self) -> None:
        """Walk pages until a page is entirely ≤ the discovery cursor, process new inbound.

        The message listing does not guarantee strict newest-first ordering —
        the discovery-cursor message itself can appear at index 0 with newer
        messages further down. So sort each page locally by
        (created_at, message_id) descending and only terminate pagination
        when the entire sorted page is ≤ the cursor (a within-page miss is
        not enough).

        Outbound messages count toward pagination (page_had_new) but never
        advance the discovery cursor. Otherwise a scheduled outbound at T+1
        would push the cursor past an inbound at T whose listing was delayed,
        silently dropping it on every subsequent poll. (`page_cursor` below is
        the transport pagination token, distinct from the discovery cursor.)
        """
        page_cursor: str | None = None
        seen_inbound: list[tuple[datetime, str, dict[str, Any]]] = []

        while True:
            page = await self.client.list_messages(limit=50, cursor=page_cursor)
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
                if not self.state.is_after_cursor(ts, mid):
                    continue
                page_had_new = True
                if m.get("direction") != "inbound":
                    continue  # outbound counts toward pagination, never the cursor
                seen_inbound.append((ts, mid, m))
            if not page.get("next_cursor") or not page_had_new:
                break
            page_cursor = page["next_cursor"]

        # Process oldest-first so the queue preserves arrival order and the
        # cursor advances only over the contiguous *settled* prefix.
        seen_inbound.sort(key=lambda x: (x[0], x[1]))
        pending_before = len(self.state.pending)
        commit: tuple[datetime, str] | None = None
        blocked = False
        for ts, mid, m in seen_inbound:
            if mid in self.state.pending:
                settled = True  # already tracked in the ledger; don't re-enqueue
            else:
                settled = await self._handle_summary(m)
            # Advance the cursor only across an unbroken run of settled
            # messages. A transient failure (e.g. get_message) leaves the
            # message unsettled and pins the cursor behind it, so it is retried
            # on the next poll instead of being silently skipped — closing the
            # same drop-on-failure hole the send path also guards against.
            if not settled:
                blocked = True
            elif not blocked:
                commit = (ts, mid)

        if commit is not None:
            self.state.set_cursor(*commit)
        # Persist if the cursor moved or the ledger grew (new pending entries).
        if commit is not None or len(self.state.pending) != pending_before:
            self.state.save()

    async def _handle_summary(self, summary: dict[str, Any]) -> bool:
        """Process one inbound summary.

        Returns True if the message is *settled* — enqueued for the agent, or
        intentionally dropped (spam / disallowed sender). Returns False on a
        transient failure (detail fetch errored) so the caller keeps the cursor
        pinned behind it for a later retry rather than skipping it forever.
        """
        if summary.get("direction") != "inbound":
            return True
        if summary.get("is_spam") or float(summary.get("spam_score") or 0) >= self.spam_score_threshold:
            logger.info("Dropping spam message %s (score=%s)",
                         summary.get("message_id"), summary.get("spam_score"))
            return True
        sender = summary.get("from_email", "")
        if self.allowed_senders:
            parsed = [addr for _, addr in getaddresses([sender]) if addr]
            sender_addr = parsed[0].lower() if parsed else ""
            if not sender_addr or sender_addr not in self.allowed_senders:
                logger.info("Skipping email from %s (not in allowed_senders)", sender)
                return True

        try:
            detail = await self.client.get_message(summary["message_id"])
        except FastmailError:
            logger.exception("Failed to fetch detail for %s", summary.get("message_id"))
            return False

        await self._enqueue_detail(detail, sender)
        return True

    async def _enqueue_detail(self, detail: dict[str, Any], sender: str) -> None:
        """Build an IncomingMessage from a message detail, record it in the
        pending-reply ledger, and enqueue it.

        The ledger entry is added BEFORE the enqueue so the message is durably
        tracked the instant the agent learns about it — a crash between here and
        a confirmed reply leaves a recoverable `queued` record.
        """
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

        message_id = detail["message_id"]
        reply_address = {
            "to": sender,
            "subject": reply_subject,
            "in_reply_to": message_id,
        }
        self.state.add_pending(
            message_id,
            created_at=self._parse_ts(detail.get("created_at", "")),
            thread_id=thread_id,
            reply_address=reply_address,
        )
        incoming = IncomingMessage(
            content=content,
            channel="email",
            session_id=thread_id,
            reply_address=reply_address,
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
            fname = _safe_attachment_filename(fname_raw)
            if fname is None:
                logger.warning(
                    "Dropping email attachment with unsafe filename %r in thread %s",
                    fname_raw, thread_id,
                )
                continue
            if fname != fname_raw:
                logger.info(
                    "Normalized email attachment filename %r -> %r in thread %s",
                    fname_raw, fname, thread_id,
                )
            mime = att.get("content_type") or "application/octet-stream"
            declared_size = int(att.get("size") or 0)
            reason = _validate_attachment_metadata(mime, declared_size)
            if reason:
                logger.warning("Dropping email attachment %s: %s", fname, reason)
                continue
            dest = out_dir / fname
            if not _assert_within(out_dir, dest):
                logger.warning(
                    "Dropping email attachment %s: resolves outside %s", fname, out_dir,
                )
                continue
            try:
                await self.client.download_attachment(detail["message_id"], att_id, dest)
            except FastmailError:
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
        """Send a reply via Fastmail SMTP. Routes to send_reply for text-only,
        send_with_attachments when attaching.

        On failure for a ledger-tracked inbound, the computed reply is persisted
        to the pending-reply ledger (status=retry) so the drain loop can re-send
        it without re-running the agent — the message is never silently dropped.
        Replies with no matching ledger entry (proactive / non-email-originated)
        are best-effort: a failure is logged, nothing is queued.
        """
        if not msg.final or not msg.content:
            return
        in_reply_to = msg.reply_address.get("in_reply_to")
        to = msg.reply_address.get("to")
        subject = msg.reply_address.get("subject")
        if not in_reply_to or not to:
            logger.error("Email send missing in_reply_to or to (got %s)", msg.reply_address)
            return

        paths = [a["path"] for a in (msg.attachments or []) if a.get("path")]
        payload = {
            "to": to,
            "subject": subject or "",
            "text_body": msg.content,
            "html_body": _render_html(msg.content) or None,
            "attachment_paths": paths,
        }
        try:
            await self._dispatch_reply(in_reply_to, payload)
        except FastmailError as e:
            self._note_failure()
            if in_reply_to in self.state.pending:
                self._schedule_retry(in_reply_to, payload, e)
                logger.warning(
                    "Reply send failed for %s; queued for retry: %s", in_reply_to, e
                )
            else:
                logger.exception("Failed to send reply for thread %s", msg.session_id)
            return

        self._note_success()
        if in_reply_to in self.state.pending:
            self.state.ack(in_reply_to)
            self.state.save()

    async def _dispatch_reply(self, in_reply_to: str, payload: dict[str, Any]) -> None:
        """Send a reply payload via the appropriate Fastmail SMTP method."""
        if payload.get("attachment_paths"):
            await self.client.send_with_attachments(
                in_reply_to=in_reply_to,
                to=payload["to"],
                subject=payload.get("subject", ""),
                text_body=payload["text_body"],
                attachment_paths=payload["attachment_paths"],
                html_body=payload.get("html_body"),
            )
        else:
            await self.client.send_reply(
                in_reply_to=in_reply_to,
                to=payload["to"],
                text_body=payload["text_body"],
                html_body=payload.get("html_body"),
            )

    def _next_retry_at(self, attempts_after: int) -> datetime:
        """Exponential backoff: backoff * 2**(attempts-1) seconds from now."""
        delay = self.config.send_retry_backoff_sec * (2 ** max(attempts_after - 1, 0))
        return self._now() + timedelta(seconds=delay)

    def _schedule_retry(self, message_id: str, payload: dict[str, Any], error: Exception) -> None:
        pr = self.state.pending.get(message_id)
        attempts_after = (pr.attempts if pr else 0) + 1
        self.state.mark_retry(
            message_id,
            reply_payload=payload,
            next_retry_at=self._next_retry_at(attempts_after),
            error=str(error),
        )
        self.state.save()

    async def _drain_retries(self, now: datetime | None = None) -> None:
        """Re-send replies whose retry backoff has elapsed (no agent re-run).

        On success the ledger entry is acked. On repeated failure attempts are
        bumped with exponential backoff until `send_max_retries`, at which point
        the entry is dead-lettered (status=dead), logged at ERROR, and escalated.
        """
        now = now or self._now()
        for mid, pr in self.state.due_retries(now):
            payload = pr.reply_payload or {}
            try:
                await self._dispatch_reply(mid, payload)
            except FastmailError as e:
                self._note_failure()
                if pr.attempts + 1 >= self.config.send_max_retries:
                    self.state.mark_dead(mid)
                    self.state.save()
                    logger.error(
                        "Reply to %s dead-lettered after %d attempts: %s",
                        mid, pr.attempts + 1, e,
                    )
                    self._escalate(
                        f"email reply to {mid} dead-lettered after "
                        f"{pr.attempts + 1} attempts: {e}"
                    )
                else:
                    self._schedule_retry(mid, payload, e)
                    logger.warning("Retry of reply to %s failed, will retry: %s", mid, e)
                continue
            self._note_success()
            self.state.ack(mid)
            self.state.save()
            logger.info("Reply to %s re-sent successfully after retry", mid)

    def _note_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures == self.config.failure_alert_threshold:
            self._escalate(
                f"{self._consecutive_failures} consecutive email send/poll failures"
            )

    def _note_success(self) -> None:
        self._consecutive_failures = 0

    def _escalate(self, reason: str) -> None:
        """Surface a persistent failure. Baseline is an ERROR log; an optional
        operator-notification hook can be wired via `_escalate_hook`."""
        logger.error("EMAIL ESCALATION: %s", reason)
        if self._escalate_hook is not None:
            try:
                self._escalate_hook(reason)
            except Exception:
                logger.exception("Email escalation hook failed")
