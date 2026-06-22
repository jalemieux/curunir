"""IMAP/SMTP client for Fastmail (custom domain curunir.ai).

Scope: only the surface the email channel needs. It deliberately mirrors the
method names of the old deadsimple REST client (validate_inbox / list_messages /
get_message / download_attachment / send_reply / send_with_attachments /
send_email) so `src/channels/email.py` and its ledger/cursor machinery stay
transport-agnostic.

Transport is stdlib `imaplib` + `smtplib` + the `email` package, wrapped in
`asyncio.to_thread` (the codebase's standard pattern for sync I/O) — zero new
dependencies. The channel polls once per ~60s, so per-call connections are
fine; there is no long-lived socket to manage.

Normalization contract (what the channel relies on):
  * `message_id`  — the RFC822 `Message-ID` header, angle brackets included.
  * `created_at`  — the `Date` header (falling back to IMAP INTERNALDATE),
                    rendered as a timezone-aware ISO-8601 string the channel's
                    `_parse_ts` can read. Naive dates are coerced to UTC so the
                    cursor comparison never mixes naive/aware datetimes.
  * `thread_id`   — the root of the `References` chain (else `In-Reply-To`, else
                    the message's own `Message-ID`) so a thread maps to one
                    session id, exactly like the old server-side thread id.
  * `direction`   — always "inbound"; INBOX is inbound-only, so the deadsimple
                    "outbound counts toward pagination" subtlety disappears.

Spam: Fastmail filters spam server-side into a Junk folder we never poll, so
INBOX is pre-filtered. We do not synthesize a spam score; the channel's guard
(`float(summary.get("spam_score") or 0)`) treats a missing score as 0.
"""
from __future__ import annotations

import asyncio
import email
import email.utils
import hashlib
import imaplib
import logging
import mimetypes
import smtplib
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid, parsedate_to_datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FastmailError(Exception):
    """Raised when an IMAP/SMTP operation against Fastmail fails."""


# --- Pure header / MIME helpers (unit-tested directly) -------------------


def _decode_mime_header(value: str | None) -> str:
    """Decode RFC 2047 encoded-words (e.g. =?utf-8?B?...?=) to a str."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 — never let a malformed header crash polling
        return value


def _ids(header_value: str | None) -> list[str]:
    """Split a Message-ID-list header (`References`/`In-Reply-To`) into ids."""
    if not header_value:
        return []
    return [tok for tok in header_value.split() if tok.strip()]


def _thread_root(*, message_id: str, references: str, in_reply_to: str) -> str:
    """Derive a stable thread id: References root → In-Reply-To → own id.

    Mirrors the old server-assigned thread id so every message in a thread maps
    to a single session id.
    """
    refs = _ids(references)
    if refs:
        return refs[0]
    irt = _ids(in_reply_to)
    if irt:
        return irt[0]
    return (message_id or "").strip()


def _to_iso_utc(dt: datetime | None) -> str | None:
    """Render a datetime as an ISO-8601 string, coercing naive → UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _normalize_envelope(header_bytes: bytes, internaldate: datetime | None) -> dict[str, Any]:
    """Map an inbound message's envelope headers to the channel's dict shape."""
    msg = email.message_from_bytes(header_bytes)
    message_id = (msg.get("Message-ID") or "").strip()
    created = None
    date_hdr = msg.get("Date")
    if date_hdr:
        try:
            created = parsedate_to_datetime(date_hdr)
        except (TypeError, ValueError):
            created = None
    if created is None:
        created = internaldate
    return {
        "message_id": message_id,
        "created_at": _to_iso_utc(created),
        "from_email": (msg.get("From") or "").strip(),
        "subject": _decode_mime_header(msg.get("Subject")),
        "thread_id": _thread_root(
            message_id=message_id,
            references=msg.get("References") or "",
            in_reply_to=msg.get("In-Reply-To") or "",
        ),
        "direction": "inbound",
    }


def _iter_attachment_parts(msg: email.message.Message):
    """Yield the message's attachment parts in a stable order.

    A part is an attachment if it has a filename or an explicit attachment
    Content-Disposition. Both `_parse_detail` and `download_attachment` walk
    with this same predicate so positional `attachment_id`s line up.
    """
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disposition = (part.get_content_disposition() or "").lower()
        if filename or disposition == "attachment":
            yield part


def _parse_detail(raw_bytes: bytes) -> dict[str, Any]:
    """Parse a full RFC822 message into the channel's detail dict."""
    msg = email.message_from_bytes(raw_bytes)
    message_id = (msg.get("Message-ID") or "").strip()

    text_body = ""
    html_body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            if part.get_filename() or (part.get_content_disposition() or "").lower() == "attachment":
                continue
            ctype = part.get_content_type()
            try:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="replace") if payload else ""
            except (LookupError, ValueError):
                decoded = part.get_payload() or ""
            if ctype == "text/plain" and not text_body:
                text_body = decoded
            elif ctype == "text/html" and not html_body:
                html_body = decoded
    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"
        decoded = payload.decode(charset, errors="replace") if payload else ""
        if msg.get_content_type() == "text/html":
            html_body = decoded
        else:
            text_body = decoded

    attachments: list[dict[str, Any]] = []
    for idx, part in enumerate(_iter_attachment_parts(msg)):
        data = part.get_payload(decode=True) or b""
        attachments.append({
            "attachment_id": str(idx),
            "filename": _decode_mime_header(part.get_filename()) or f"attachment-{idx}",
            "content_type": part.get_content_type() or "application/octet-stream",
            "size": len(data),
        })

    date_hdr = msg.get("Date")
    created = None
    if date_hdr:
        try:
            created = parsedate_to_datetime(date_hdr)
        except (TypeError, ValueError):
            created = None

    return {
        "message_id": message_id,
        "from_email": (msg.get("From") or "").strip(),
        "subject": _decode_mime_header(msg.get("Subject")),
        "text_body": text_body,
        "html_body": html_body,
        "attachments": attachments,
        "created_at": _to_iso_utc(created),
        "direction": "inbound",
        "thread_id": _thread_root(
            message_id=message_id,
            references=msg.get("References") or "",
            in_reply_to=msg.get("In-Reply-To") or "",
        ),
    }


def _guess_content_type(filename: str) -> tuple[str, str]:
    mime, _ = mimetypes.guess_type(filename)
    maintype, _, subtype = (mime or "application/octet-stream").partition("/")
    return maintype or "application", subtype or "octet-stream"


def _stable_reply_msgid(in_reply_to: str, domain: str) -> str:
    """A deterministic Message-ID for a reply, reused across retries.

    SMTP has no idempotency key, so a re-send after a lost ack must carry the
    same Message-ID — that lets the receiving server dedupe the duplicate.
    """
    digest = hashlib.sha1(in_reply_to.encode("utf-8", "replace")).hexdigest()[:24]
    return f"<reply-{digest}@{domain}>"


# --- Client --------------------------------------------------------------


class FastmailClient:
    def __init__(
        self,
        *,
        imap_host: str,
        smtp_host: str,
        user: str,
        password: str,
        inbox: str,
        allowed_recipients: list[str],
        restrict_outbound: bool,
        imap_port: int = 993,
        smtp_port: int = 465,
        timeout_sec: float = 30.0,
        fetch_limit: int = 50,
    ):
        self.imap_host = imap_host
        self.smtp_host = smtp_host
        self.user = user
        self.password = password
        self.inbox = inbox
        self.allowed_recipients = [r.lower() for r in allowed_recipients]
        self.restrict_outbound = restrict_outbound
        self.imap_port = imap_port
        self.smtp_port = smtp_port
        self.timeout_sec = timeout_sec
        self.fetch_limit = fetch_limit

    async def aclose(self) -> None:
        """No persistent connection to close (per-call IMAP/SMTP sessions)."""
        return None

    @property
    def _domain(self) -> str:
        _, _, domain = self.inbox.partition("@")
        return domain or "localhost"

    # --- IMAP session helper --------------------------------------------

    def _connect_imap(self) -> imaplib.IMAP4_SSL:
        try:
            imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
            imap.login(self.user, self.password)
        except (imaplib.IMAP4.error, OSError) as e:
            raise FastmailError(f"IMAP connect/login failed: {e}") from e
        except Exception as e:  # noqa: BLE001 — surface any login failure uniformly
            raise FastmailError(f"IMAP connect/login failed: {e}") from e
        return imap

    @staticmethod
    def _logout(imap: imaplib.IMAP4_SSL) -> None:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001 — logout failures are non-fatal
            pass

    # --- validate_inbox -------------------------------------------------

    async def validate_inbox(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._validate_inbox_sync)

    def _validate_inbox_sync(self) -> dict[str, Any]:
        imap = self._connect_imap()
        try:
            status, _ = imap.select("INBOX", readonly=True)
            if status != "OK":
                raise FastmailError(f"IMAP SELECT INBOX failed: {status}")
        finally:
            self._logout(imap)
        return {"email": self.inbox}

    # --- list_messages --------------------------------------------------

    async def list_messages(self, *, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        """Return the most-recent inbound messages from INBOX, normalized.

        IMAP delivers all matching UIDs at once, so there is no real pagination:
        we fetch the newest `limit` UIDs and return `next_cursor=None`. The
        channel's discovery cursor remains authoritative for what counts as new.
        """
        return await asyncio.to_thread(self._list_messages_sync, limit)

    def _list_messages_sync(self, limit: int) -> dict[str, Any]:
        imap = self._connect_imap()
        try:
            status, _ = imap.select("INBOX", readonly=True)
            if status != "OK":
                raise FastmailError(f"IMAP SELECT INBOX failed: {status}")
            status, data = imap.uid("search", None, "ALL")
            if status != "OK":
                raise FastmailError(f"IMAP SEARCH failed: {status}")
            uids = (data[0].split() if data and data[0] else [])
            if not uids:
                return {"messages": [], "next_cursor": None}
            recent = uids[-limit:]
            messages: list[dict[str, Any]] = []
            for uid in recent:
                normalized = self._fetch_envelope(imap, uid)
                if normalized is not None:
                    messages.append(normalized)
            return {"messages": messages, "next_cursor": None}
        finally:
            self._logout(imap)

    def _fetch_envelope(self, imap: imaplib.IMAP4_SSL, uid: bytes) -> dict[str, Any] | None:
        status, data = imap.uid(
            "fetch",
            uid,
            "(INTERNALDATE BODY.PEEK[HEADER.FIELDS "
            "(MESSAGE-ID DATE FROM SUBJECT REFERENCES IN-REPLY-TO)])",
        )
        if status != "OK" or not data:
            return None
        header_bytes = _first_literal(data)
        if header_bytes is None:
            return None
        internaldate = _extract_internaldate(data, imap)
        return _normalize_envelope(header_bytes, internaldate)

    # --- get_message ----------------------------------------------------

    async def get_message(self, message_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_message_sync, message_id)

    def _get_message_sync(self, message_id: str) -> dict[str, Any]:
        imap = self._connect_imap()
        try:
            raw = self._fetch_rfc822(imap, message_id)
        finally:
            self._logout(imap)
        return _parse_detail(raw)

    def _fetch_rfc822(self, imap: imaplib.IMAP4_SSL, message_id: str) -> bytes:
        status, _ = imap.select("INBOX", readonly=True)
        if status != "OK":
            raise FastmailError(f"IMAP SELECT INBOX failed: {status}")
        status, data = imap.uid("search", None, "HEADER", "Message-ID", message_id)
        if status != "OK":
            raise FastmailError(f"IMAP SEARCH for {message_id} failed: {status}")
        uids = (data[0].split() if data and data[0] else [])
        if not uids:
            raise FastmailError(f"message {message_id} not found in INBOX")
        uid = uids[-1]
        status, fetched = imap.uid("fetch", uid, "(RFC822)")
        if status != "OK":
            raise FastmailError(f"IMAP FETCH for {message_id} failed: {status}")
        raw = _first_literal(fetched)
        if raw is None:
            raise FastmailError(f"empty RFC822 fetch for {message_id}")
        return raw

    # --- download_attachment --------------------------------------------

    async def download_attachment(self, message_id: str, attachment_id: str, dest: Path) -> None:
        await asyncio.to_thread(self._download_attachment_sync, message_id, attachment_id, dest)

    def _download_attachment_sync(self, message_id: str, attachment_id: str, dest: Path) -> None:
        imap = self._connect_imap()
        try:
            raw = self._fetch_rfc822(imap, message_id)
        finally:
            self._logout(imap)
        msg = email.message_from_bytes(raw)
        try:
            idx = int(attachment_id)
        except (TypeError, ValueError) as e:
            raise FastmailError(f"bad attachment id {attachment_id!r}") from e
        parts = list(_iter_attachment_parts(msg))
        if idx < 0 or idx >= len(parts):
            raise FastmailError(
                f"attachment {attachment_id} out of range for {message_id}"
            )
        data = parts[idx].get_payload(decode=True) or b""
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    # --- Outbound -------------------------------------------------------

    def _check_recipients_allowed(self, *recipients: str | None) -> None:
        if not self.restrict_outbound or not self.allowed_recipients:
            return
        raw = [r for r in recipients if r]
        parsed = [addr.lower() for _, addr in getaddresses(raw) if addr]
        blocked = [r for r in parsed if r not in self.allowed_recipients]
        if blocked or not parsed:
            raise FastmailError(
                f"Outbound email blocked: recipient(s) {blocked or list(raw)} not in allowlist "
                f"({self.allowed_recipients}). Set EMAIL_RESTRICT_OUTBOUND=false to disable."
            )

    def _build_message(
        self,
        *,
        to: list[str],
        subject: str,
        text_body: str,
        html_body: str | None,
        message_id: str,
        cc: list[str] | None = None,
        in_reply_to: str | None = None,
        attachment_paths: list[str] | None = None,
    ) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.inbox
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = message_id
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        msg.set_content(text_body)
        if html_body:
            msg.add_alternative(html_body, subtype="html")
        for p in attachment_paths or []:
            path = Path(p)
            maintype, subtype = _guess_content_type(path.name)
            msg.add_attachment(
                path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name
            )
        return msg

    def _smtp_send(self, msg: EmailMessage) -> None:
        try:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=self.timeout_sec) as smtp:
                smtp.login(self.user, self.password)
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            raise FastmailError(f"SMTP send failed: {e}") from e

    async def send_reply(
        self,
        *,
        in_reply_to: str,
        to: str,
        text_body: str,
        subject: str = "",
        html_body: str | None = None,
    ) -> dict[str, Any]:
        self._check_recipients_allowed(to)
        message_id = _stable_reply_msgid(in_reply_to, self._domain)
        msg = self._build_message(
            to=[to], subject=subject, text_body=text_body, html_body=html_body,
            message_id=message_id, in_reply_to=in_reply_to,
        )
        await asyncio.to_thread(self._smtp_send, msg)
        return {"id": message_id}

    async def send_with_attachments(
        self,
        *,
        in_reply_to: str,
        to: str,
        subject: str,
        text_body: str,
        attachment_paths: list[str],
        html_body: str | None = None,
    ) -> dict[str, Any]:
        self._check_recipients_allowed(to)
        message_id = _stable_reply_msgid(in_reply_to, self._domain)
        msg = self._build_message(
            to=[to], subject=subject, text_body=text_body, html_body=html_body,
            message_id=message_id, in_reply_to=in_reply_to,
            attachment_paths=attachment_paths,
        )
        await asyncio.to_thread(self._smtp_send, msg)
        return {"id": message_id}

    async def send_email(
        self,
        *,
        to: str | list[str],
        subject: str,
        text_body: str,
        html_body: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        attachment_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send a new (cold) outbound email — no threading headers."""
        to_list = [to] if isinstance(to, str) else list(to)
        self._check_recipients_allowed(*to_list, *(cc or []), *(bcc or []))
        message_id = make_msgid(domain=self._domain)
        msg = self._build_message(
            to=to_list, subject=subject, text_body=text_body, html_body=html_body,
            message_id=message_id, cc=cc, attachment_paths=attachment_paths,
        )
        # bcc recipients are added to the envelope but not a visible header.
        bcc_list = list(bcc or [])
        if bcc_list:
            envelope_to = to_list + list(cc or []) + bcc_list
            await asyncio.to_thread(self._smtp_send_to, msg, envelope_to)
        else:
            await asyncio.to_thread(self._smtp_send, msg)
        return {"id": message_id}

    def _smtp_send_to(self, msg: EmailMessage, recipients: list[str]) -> None:
        try:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=self.timeout_sec) as smtp:
                smtp.login(self.user, self.password)
                smtp.send_message(msg, to_addrs=recipients)
        except (smtplib.SMTPException, OSError) as e:
            raise FastmailError(f"SMTP send failed: {e}") from e


# --- IMAP FETCH response helpers -----------------------------------------


def _first_literal(fetch_data: Any) -> bytes | None:
    """Return the first literal (bytes) payload from an imaplib FETCH response.

    imaplib returns a list where header/body-bearing items are 2-tuples of
    (metadata_bytes, literal_bytes); flat bytes items are response framing.
    """
    if not fetch_data:
        return None
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def _extract_internaldate(fetch_data: Any, imap: imaplib.IMAP4_SSL) -> datetime | None:
    """Pull INTERNALDATE from a FETCH response's metadata, if present."""
    for item in fetch_data:
        meta = item[0] if isinstance(item, tuple) else item
        if not isinstance(meta, (bytes, bytearray)):
            continue
        if b"INTERNALDATE" not in meta:
            continue
        try:
            parsed = imaplib.Internaldate2tuple(bytes(meta))
        except Exception:  # noqa: BLE001
            parsed = None
        if parsed is not None:
            import calendar
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    return None


def build_client_from_env() -> "FastmailClient":
    """Build a FastmailClient from env vars — convenience for one-shot scripts.

    Reads FASTMAIL_USER, FASTMAIL_PASSWORD, FASTMAIL_INBOX, optional
    FASTMAIL_IMAP_HOST / FASTMAIL_SMTP_HOST, EMAIL_ALLOWED_SENDERS, and
    EMAIL_RESTRICT_OUTBOUND. Raises FastmailError if a credential is missing.
    """
    import os
    user = os.environ.get("FASTMAIL_USER", "").strip()
    password = os.environ.get("FASTMAIL_PASSWORD", "").strip()
    inbox = os.environ.get("FASTMAIL_INBOX", "").strip() or user
    if not user or not password or not inbox:
        raise FastmailError(
            "FASTMAIL_USER, FASTMAIL_PASSWORD, and FASTMAIL_INBOX must be set in the environment"
        )
    return FastmailClient(
        imap_host=os.environ.get("FASTMAIL_IMAP_HOST", "imap.fastmail.com"),
        smtp_host=os.environ.get("FASTMAIL_SMTP_HOST", "smtp.fastmail.com"),
        user=user,
        password=password,
        inbox=inbox,
        allowed_recipients=[
            s.strip() for s in os.environ.get("EMAIL_ALLOWED_SENDERS", "").split(",") if s.strip()
        ],
        restrict_outbound=os.environ.get("EMAIL_RESTRICT_OUTBOUND", "true").lower() == "true",
    )
