"""Tests for the Fastmail IMAP/SMTP client (src/channels/fastmail.py).

The client exposes the same method surface the email channel already calls
(validate_inbox / list_messages / get_message / download_attachment /
send_reply / send_with_attachments / send_email) but implemented over stdlib
imaplib + smtplib wrapped in asyncio.to_thread. No network: imaplib.IMAP4_SSL
and smtplib.SMTP_SSL are mocked.
"""
from __future__ import annotations

import base64
import email
from datetime import datetime, timezone
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest

from src.channels.fastmail import (
    FastmailClient,
    FastmailError,
    _normalize_envelope,
    _parse_detail,
    _thread_root,
)


# --- Fixtures ------------------------------------------------------------

@pytest.fixture
def client():
    return FastmailClient(
        imap_host="imap.fastmail.com",
        smtp_host="smtp.fastmail.com",
        user="jac@curunir.ai",
        password="app-password",
        inbox="jac@curunir.ai",
        allowed_recipients=[],
        restrict_outbound=False,
    )


@pytest.fixture
def restricted_client():
    return FastmailClient(
        imap_host="imap.fastmail.com",
        smtp_host="smtp.fastmail.com",
        user="jac@curunir.ai",
        password="app-password",
        inbox="jac@curunir.ai",
        allowed_recipients=["alice@example.com"],
        restrict_outbound=True,
    )


def _header_bytes(**headers: str) -> bytes:
    return ("\r\n".join(f"{k}: {v}" for k, v in headers.items()) + "\r\n\r\n").encode()


# --- _thread_root --------------------------------------------------------

def test_thread_root_uses_references_root():
    """The first id in References is the thread root."""
    root = _thread_root(
        message_id="<child@x.com>",
        references="<root@x.com> <mid@x.com>",
        in_reply_to="<mid@x.com>",
    )
    assert root == "<root@x.com>"


def test_thread_root_falls_back_to_in_reply_to():
    root = _thread_root(
        message_id="<child@x.com>", references="", in_reply_to="<parent@x.com>"
    )
    assert root == "<parent@x.com>"


def test_thread_root_falls_back_to_own_message_id():
    root = _thread_root(message_id="<solo@x.com>", references="", in_reply_to="")
    assert root == "<solo@x.com>"


# --- _normalize_envelope -------------------------------------------------

def test_normalize_envelope_maps_headers_to_channel_dict():
    raw = _header_bytes(**{
        "Message-ID": "<m1@curunir.ai>",
        "Date": "Thu, 14 May 2026 15:30:00 +0000",
        "From": '"Alice" <alice@example.com>',
        "Subject": "hello there",
    })
    msg = _normalize_envelope(raw, internaldate=None)
    assert msg["message_id"] == "<m1@curunir.ai>"
    assert msg["from_email"] == '"Alice" <alice@example.com>'
    assert msg["subject"] == "hello there"
    assert msg["direction"] == "inbound"
    # created_at is an ISO-8601 string the channel can parse, tz-aware UTC.
    parsed = datetime.fromisoformat(msg["created_at"])
    assert parsed == datetime(2026, 5, 14, 15, 30, 0, tzinfo=timezone.utc)


def test_normalize_envelope_thread_id_is_references_root():
    raw = _header_bytes(**{
        "Message-ID": "<child@curunir.ai>",
        "Date": "Thu, 14 May 2026 15:30:00 +0000",
        "From": "alice@example.com",
        "Subject": "Re: hi",
        "References": "<root@curunir.ai> <mid@curunir.ai>",
        "In-Reply-To": "<mid@curunir.ai>",
    })
    msg = _normalize_envelope(raw, internaldate=None)
    assert msg["thread_id"] == "<root@curunir.ai>"


def test_normalize_envelope_thread_id_defaults_to_own_id():
    raw = _header_bytes(**{
        "Message-ID": "<solo@curunir.ai>",
        "Date": "Thu, 14 May 2026 15:30:00 +0000",
        "From": "alice@example.com",
        "Subject": "fresh",
    })
    msg = _normalize_envelope(raw, internaldate=None)
    assert msg["thread_id"] == "<solo@curunir.ai>"


def test_normalize_envelope_decodes_mime_subject():
    raw = _header_bytes(**{
        "Message-ID": "<m1@curunir.ai>",
        "Date": "Thu, 14 May 2026 15:30:00 +0000",
        "From": "alice@example.com",
        "Subject": "=?utf-8?B?aGVsbG8gd29ybGQ=?=",  # "hello world"
    })
    msg = _normalize_envelope(raw, internaldate=None)
    assert msg["subject"] == "hello world"


def test_normalize_envelope_falls_back_to_internaldate_when_no_date_header():
    raw = _header_bytes(**{
        "Message-ID": "<m1@curunir.ai>",
        "From": "alice@example.com",
        "Subject": "no date",
    })
    internal = datetime(2026, 5, 14, 15, 30, 0, tzinfo=timezone.utc)
    msg = _normalize_envelope(raw, internaldate=internal)
    assert datetime.fromisoformat(msg["created_at"]) == internal


def test_normalize_envelope_naive_date_is_made_utc_aware():
    raw = _header_bytes(**{
        "Message-ID": "<m1@curunir.ai>",
        "Date": "Thu, 14 May 2026 15:30:00",  # no offset
        "From": "alice@example.com",
        "Subject": "naive",
    })
    msg = _normalize_envelope(raw, internaldate=None)
    parsed = datetime.fromisoformat(msg["created_at"])
    assert parsed.tzinfo is not None  # comparison against aware cursor must not raise


# --- _parse_detail -------------------------------------------------------

def _build_inbound(
    *, message_id="<m1@curunir.ai>", text="Hello there", html="<p>Hello</p>",
    attachments=None,
) -> bytes:
    msg = EmailMessage()
    msg["Message-ID"] = message_id
    msg["From"] = "alice@example.com"
    msg["To"] = "jac@curunir.ai"
    msg["Subject"] = "hi"
    msg["Date"] = "Thu, 14 May 2026 15:30:00 +0000"
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    for fname, ctype, data in attachments or []:
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=fname)
    return msg.as_bytes()


def test_parse_detail_extracts_text_html_and_metadata():
    raw = _build_inbound()
    detail = _parse_detail(raw)
    assert detail["message_id"] == "<m1@curunir.ai>"
    assert detail["from_email"] == "alice@example.com"
    assert detail["subject"] == "hi"
    assert detail["direction"] == "inbound"
    assert "Hello there" in detail["text_body"]
    assert "<p>Hello</p>" in detail["html_body"]
    assert detail["attachments"] == []


def test_parse_detail_lists_attachments_with_metadata():
    raw = _build_inbound(attachments=[("report.pdf", "application/pdf", b"PDFDATA")])
    detail = _parse_detail(raw)
    assert len(detail["attachments"]) == 1
    att = detail["attachments"][0]
    assert att["filename"] == "report.pdf"
    assert att["content_type"] == "application/pdf"
    assert att["size"] == len(b"PDFDATA")
    # attachment_id is a stable positional index the channel passes back.
    assert att["attachment_id"] == "0"


def test_parse_detail_thread_id_from_references():
    msg = EmailMessage()
    msg["Message-ID"] = "<child@curunir.ai>"
    msg["From"] = "alice@example.com"
    msg["Subject"] = "Re: hi"
    msg["References"] = "<root@curunir.ai> <mid@curunir.ai>"
    msg.set_content("body")
    detail = _parse_detail(msg.as_bytes())
    assert detail["thread_id"] == "<root@curunir.ai>"


# --- list_messages (mocked IMAP) -----------------------------------------

class _FakeIMAP:
    """Minimal imaplib.IMAP4_SSL stand-in driven by canned responses."""

    def __init__(self, *, search_uids=b"", fetch_map=None, header_fetch=None):
        self.login = MagicMock(return_value=("OK", [b"logged in"]))
        self.logout = MagicMock(return_value=("BYE", [b""]))
        self.select = MagicMock(return_value=("OK", [b"1"]))
        self._search_uids = search_uids
        self._fetch_map = fetch_map or {}
        self._header_fetch = header_fetch

    def uid(self, command, *args):
        command = command.lower()
        if command == "search":
            # last positional element decides ALL vs HEADER lookups
            if args and args[1:] and str(args[1]).upper() == "HEADER":
                # HEADER Message-ID "<...>"
                mid = args[-1]
                uid = self._fetch_map.get(("byheader", mid))
                return ("OK", [uid or b""])
            return ("OK", [self._search_uids])
        if command == "fetch":
            uid = args[0]
            if isinstance(uid, bytes):
                uid = uid.decode()
            data = self._fetch_map.get(("fetch", str(uid)))
            if data is None and self._header_fetch is not None:
                data = self._header_fetch
            return ("OK", data)
        return ("OK", [b""])


def test_list_messages_returns_normalized_dicts(client):
    header_bytes = _header_bytes(**{
        "Message-ID": "<m1@curunir.ai>",
        "Date": "Thu, 14 May 2026 15:30:00 +0000",
        "From": "alice@example.com",
        "Subject": "hi",
    })
    fake = _FakeIMAP(
        search_uids=b"1001",
        fetch_map={("fetch", "1001"): [(b"1001 (UID 1001 INTERNALDATE \"...\"", header_bytes), b")"]},
    )
    with patch("src.channels.fastmail.imaplib.IMAP4_SSL", return_value=fake):
        import asyncio
        page = asyncio.run(client.list_messages(limit=50))
    assert page["next_cursor"] is None
    assert len(page["messages"]) == 1
    m = page["messages"][0]
    assert m["message_id"] == "<m1@curunir.ai>"
    assert m["direction"] == "inbound"
    assert m["from_email"] == "alice@example.com"


def test_list_messages_empty_inbox(client):
    fake = _FakeIMAP(search_uids=b"")
    with patch("src.channels.fastmail.imaplib.IMAP4_SSL", return_value=fake):
        import asyncio
        page = asyncio.run(client.list_messages(limit=50))
    assert page["messages"] == []
    assert page["next_cursor"] is None


# --- get_message (mocked IMAP) -------------------------------------------

def test_get_message_returns_detail(client):
    raw = _build_inbound()
    fake = _FakeIMAP(
        fetch_map={
            ("byheader", "<m1@curunir.ai>"): b"1001",
            ("fetch", "1001"): [(b"1001 (UID 1001 RFC822 {123}", raw), b")"],
        },
    )
    with patch("src.channels.fastmail.imaplib.IMAP4_SSL", return_value=fake):
        import asyncio
        detail = asyncio.run(client.get_message("<m1@curunir.ai>"))
    assert detail["message_id"] == "<m1@curunir.ai>"
    assert "Hello there" in detail["text_body"]


def test_get_message_raises_when_not_found(client):
    fake = _FakeIMAP(fetch_map={("byheader", "<missing@curunir.ai>"): b""})
    with patch("src.channels.fastmail.imaplib.IMAP4_SSL", return_value=fake):
        import asyncio
        with pytest.raises(FastmailError):
            asyncio.run(client.get_message("<missing@curunir.ai>"))


def test_download_attachment_writes_bytes(client, tmp_path):
    raw = _build_inbound(attachments=[("report.pdf", "application/pdf", b"PDFBYTES")])
    fake = _FakeIMAP(
        fetch_map={
            ("byheader", "<m1@curunir.ai>"): b"1001",
            ("fetch", "1001"): [(b"1001 (UID 1001 RFC822 {123}", raw), b")"],
        },
    )
    dest = tmp_path / "out.pdf"
    with patch("src.channels.fastmail.imaplib.IMAP4_SSL", return_value=fake):
        import asyncio
        asyncio.run(client.download_attachment("<m1@curunir.ai>", "0", dest))
    assert dest.read_bytes() == b"PDFBYTES"


# --- validate_inbox ------------------------------------------------------

def test_validate_inbox_returns_email_on_success(client):
    fake = _FakeIMAP()
    with patch("src.channels.fastmail.imaplib.IMAP4_SSL", return_value=fake):
        import asyncio
        info = asyncio.run(client.validate_inbox())
    assert info["email"] == "jac@curunir.ai"
    fake.login.assert_called_once()
    fake.select.assert_called_once()


def test_validate_inbox_raises_on_login_failure(client):
    fake = _FakeIMAP()
    fake.login.side_effect = Exception("auth failed")
    with patch("src.channels.fastmail.imaplib.IMAP4_SSL", return_value=fake):
        import asyncio
        with pytest.raises(FastmailError):
            asyncio.run(client.validate_inbox())


# --- send_* (mocked SMTP) ------------------------------------------------

class _FakeSMTP:
    def __init__(self, *args, **kwargs):
        self.sent: list[EmailMessage] = []
        self.login = MagicMock()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send_message(self, msg, **kwargs):
        self.sent.append(msg)
        self.last_kwargs = kwargs


def _capture_smtp():
    holder = {}

    def factory(*args, **kwargs):
        smtp = _FakeSMTP(*args, **kwargs)
        holder["smtp"] = smtp
        holder.setdefault("args", args)
        return smtp

    return holder, factory


def test_send_reply_builds_threaded_mime(client):
    holder, factory = _capture_smtp()
    with patch("src.channels.fastmail.smtplib.SMTP_SSL", side_effect=factory):
        import asyncio
        asyncio.run(client.send_reply(
            in_reply_to="<m1@curunir.ai>", to="alice@example.com", text_body="Hi back",
        ))
    msg = holder["smtp"].sent[0]
    assert msg["In-Reply-To"] == "<m1@curunir.ai>"
    assert "<m1@curunir.ai>" in msg["References"]
    assert msg["To"] == "alice@example.com"
    assert msg["From"] == "jac@curunir.ai"
    assert msg["Message-ID"]  # a generated Message-ID is set
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert "Hi back" in body


def test_send_reply_message_id_is_stable_across_retries(client):
    """A reply's Message-ID is derived from in_reply_to so a re-send dedupes."""
    ids = []
    for _ in range(2):
        holder, factory = _capture_smtp()
        with patch("src.channels.fastmail.smtplib.SMTP_SSL", side_effect=factory):
            import asyncio
            asyncio.run(client.send_reply(
                in_reply_to="<m1@curunir.ai>", to="alice@example.com", text_body="Hi",
            ))
        ids.append(holder["smtp"].sent[0]["Message-ID"])
    assert ids[0] == ids[1]


def test_send_reply_includes_html_alternative(client):
    holder, factory = _capture_smtp()
    with patch("src.channels.fastmail.smtplib.SMTP_SSL", side_effect=factory):
        import asyncio
        asyncio.run(client.send_reply(
            in_reply_to="<m1@curunir.ai>", to="alice@example.com",
            text_body="Hi back", html_body="<p>Hi back</p>",
        ))
    msg = holder["smtp"].sent[0]
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert "<p>Hi back</p>" in html


def test_send_reply_blocked_by_allowlist(restricted_client):
    holder, factory = _capture_smtp()
    with patch("src.channels.fastmail.smtplib.SMTP_SSL", side_effect=factory):
        import asyncio
        with pytest.raises(FastmailError) as exc:
            asyncio.run(restricted_client.send_reply(
                in_reply_to="<m1@curunir.ai>", to="evil@evil.com", text_body="hi",
            ))
    assert "Outbound" in str(exc.value)
    assert "smtp" not in holder  # never connected


@pytest.mark.parametrize("bad_to", [
    "alice@example.com.evil.tld",
    "evilalice@example.com.attacker.tld",
    '"alice@example.com" <attacker@evil.tld>',
    "alice@example.com, evil@evil.com",
])
def test_send_reply_rejects_allowlist_bypass(restricted_client, bad_to):
    holder, factory = _capture_smtp()
    with patch("src.channels.fastmail.smtplib.SMTP_SSL", side_effect=factory):
        import asyncio
        with pytest.raises(FastmailError):
            asyncio.run(restricted_client.send_reply(
                in_reply_to="<m1@curunir.ai>", to=bad_to, text_body="hi",
            ))
    assert "smtp" not in holder


@pytest.mark.parametrize("good_to", [
    "alice@example.com",
    "Alice@Example.COM",
    '"Alice" <alice@example.com>',
])
def test_send_reply_accepts_allowed_recipient_variants(restricted_client, good_to):
    holder, factory = _capture_smtp()
    with patch("src.channels.fastmail.smtplib.SMTP_SSL", side_effect=factory):
        import asyncio
        asyncio.run(restricted_client.send_reply(
            in_reply_to="<m1@curunir.ai>", to=good_to, text_body="hi",
        ))
    assert len(holder["smtp"].sent) == 1


def test_send_with_attachments_round_trips(client, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_bytes(b"hello-bytes")
    holder, factory = _capture_smtp()
    with patch("src.channels.fastmail.smtplib.SMTP_SSL", side_effect=factory):
        import asyncio
        asyncio.run(client.send_with_attachments(
            in_reply_to="<m1@curunir.ai>", to="alice@example.com",
            subject="Re: hi", text_body="see attached", attachment_paths=[str(f)],
        ))
    msg = holder["smtp"].sent[0]
    assert msg["In-Reply-To"] == "<m1@curunir.ai>"
    assert msg["Subject"] == "Re: hi"
    atts = [p for p in msg.iter_attachments()]
    assert len(atts) == 1
    assert atts[0].get_filename() == "doc.txt"
    assert atts[0].get_content() == b"hello-bytes" or atts[0].get_payload(decode=True) == b"hello-bytes"


def test_send_with_attachments_blocked_by_allowlist(restricted_client, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_bytes(b"x")
    holder, factory = _capture_smtp()
    with patch("src.channels.fastmail.smtplib.SMTP_SSL", side_effect=factory):
        import asyncio
        with pytest.raises(FastmailError):
            asyncio.run(restricted_client.send_with_attachments(
                in_reply_to="<m1@curunir.ai>", to="evil@evil.com",
                subject="re", text_body="x", attachment_paths=[str(f)],
            ))
    assert "smtp" not in holder


def test_send_email_no_threading_headers(client):
    holder, factory = _capture_smtp()
    with patch("src.channels.fastmail.smtplib.SMTP_SSL", side_effect=factory):
        import asyncio
        result = asyncio.run(client.send_email(
            to="alice@example.com", subject="Hi", text_body="hello",
        ))
    msg = holder["smtp"].sent[0]
    assert msg["To"] == "alice@example.com"
    assert msg["Subject"] == "Hi"
    assert msg["In-Reply-To"] is None
    assert msg["References"] is None
    # returns an id dict so the CLI can report it
    assert "id" in result


def test_send_email_with_cc_bcc_html_attachments(client, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_bytes(b"data")
    holder, factory = _capture_smtp()
    with patch("src.channels.fastmail.smtplib.SMTP_SSL", side_effect=factory):
        import asyncio
        asyncio.run(client.send_email(
            to=["alice@example.com", "carol@example.com"],
            subject="Hi", text_body="hello", html_body="<p>hello</p>",
            cc=["bob@example.com"], bcc=["dan@example.com"],
            attachment_paths=[str(f)],
        ))
    msg = holder["smtp"].sent[0]
    assert msg["To"] == "alice@example.com, carol@example.com"
    assert msg["Cc"] == "bob@example.com"
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert "<p>hello</p>" in html
    atts = list(msg.iter_attachments())
    assert any(a.get_filename() == "doc.txt" for a in atts)


def test_send_email_blocked_by_allowlist(restricted_client):
    holder, factory = _capture_smtp()
    with patch("src.channels.fastmail.smtplib.SMTP_SSL", side_effect=factory):
        import asyncio
        with pytest.raises(FastmailError):
            asyncio.run(restricted_client.send_email(
                to="evil@evil.com", subject="x", text_body="x",
            ))
    assert "smtp" not in holder


def test_send_raises_fastmail_error_on_smtp_failure(client):
    def boom(*a, **k):
        raise OSError("connection refused")
    with patch("src.channels.fastmail.smtplib.SMTP_SSL", side_effect=boom):
        import asyncio
        with pytest.raises(FastmailError):
            asyncio.run(client.send_email(to="alice@example.com", subject="x", text_body="x"))


# --- build_client_from_env ----------------------------------------------

def test_build_client_from_env_raises_on_missing_creds(monkeypatch):
    from src.channels.fastmail import build_client_from_env
    for var in ("FASTMAIL_USER", "FASTMAIL_PASSWORD", "FASTMAIL_INBOX"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(FastmailError):
        build_client_from_env()


def test_build_client_from_env_constructs_client(monkeypatch):
    from src.channels.fastmail import build_client_from_env
    monkeypatch.setenv("FASTMAIL_USER", "jac@curunir.ai")
    monkeypatch.setenv("FASTMAIL_PASSWORD", "app-pass")
    monkeypatch.setenv("FASTMAIL_INBOX", "jac@curunir.ai")
    monkeypatch.setenv("EMAIL_ALLOWED_SENDERS", "a@x.com,b@x.com")
    monkeypatch.setenv("EMAIL_RESTRICT_OUTBOUND", "false")
    c = build_client_from_env()
    assert c.user == "jac@curunir.ai"
    assert c.inbox == "jac@curunir.ai"
    assert c.allowed_recipients == ["a@x.com", "b@x.com"]
    assert c.restrict_outbound is False
