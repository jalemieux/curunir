import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.email import EmailChannel
from src.channels.fastmail import FastmailError
from src.config import EmailChannelConfig


@pytest.fixture
def email_config(tmp_path):
    return EmailChannelConfig(
        enabled=True,
        imap_host="imap.fastmail.com",
        smtp_host="smtp.fastmail.com",
        user="jac@curunir.ai",
        password="app-password",
        inbox="jac@curunir.ai",
        poll_interval_sec=1,
        allowed_senders=["alice@example.com"],
        restrict_outbound=True,
        attachment_dir=str(tmp_path / "attachments"),
        state_file=tmp_path / "email_state.json",
        spam_score_threshold=5.0,
        send_max_retries=5,
        send_retry_backoff_sec=0.0,  # immediate retries keep drain tests deterministic
        failure_alert_threshold=5,
    )


@pytest.fixture
def in_queue():
    return asyncio.Queue()


def _make_channel(in_queue, config, client: AsyncMock | None = None):
    """Construct the channel with the Fastmail client patched out."""
    mock_client = client or AsyncMock()
    with patch("src.channels.email.FastmailClient", return_value=mock_client):
        ch = EmailChannel(in_queue, config)
    return ch, mock_client


def test_constructor(email_config, in_queue):
    ch, _ = _make_channel(in_queue, email_config)
    assert ch.in_queue is in_queue
    assert ch.config is email_config
    assert ch.client is not None
    assert ch.poll_interval == 1
    assert ch.allowed_senders == ["alice@example.com"]
    assert ch.attachment_dir == email_config.attachment_dir
    # State starts blank
    assert ch.state.watermark_created_at is None


@pytest.mark.asyncio
async def test_start_validates_inbox_then_initializes_watermark(email_config, in_queue):
    client = AsyncMock()
    client.validate_inbox.return_value = {"data": {"inbox_id": "inbox-uuid-1", "email": "jac@curunir.ai"}}
    client.list_messages.return_value = {"messages": [], "next_cursor": None}

    ch, _ = _make_channel(in_queue, email_config, client=client)

    # Run one poll cycle then cancel.
    async def runner():
        task = asyncio.create_task(ch.start())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await runner()

    client.validate_inbox.assert_awaited_once()
    # Watermark file created with a non-empty timestamp.
    assert email_config.state_file.exists()
    saved = json.loads(email_config.state_file.read_text())
    assert saved["watermark_created_at"] is not None


@pytest.mark.asyncio
async def test_start_returns_early_on_inbox_validation_failure(email_config, in_queue, caplog):
    client = AsyncMock()
    client.validate_inbox.side_effect = FastmailError("404: inbox not found")

    ch, _ = _make_channel(in_queue, email_config, client=client)
    await ch.start()  # returns without raising

    # No watermark file written, no list call made.
    client.list_messages.assert_not_called()


def _msg(message_id, *, ts, direction="inbound", from_email="alice@example.com",
         subject="hi", thread_id="t1", is_spam=False, spam_score=0.0):
    return {
        "message_id": message_id,
        "thread_id": thread_id,
        "direction": direction,
        "from_email": from_email,
        "subject": subject,
        "is_spam": is_spam,
        "spam_score": spam_score,
        "created_at": ts,
    }


def _detail(message_id, *, text_body="hi body", thread_id="t1", subject="hi",
            from_email="alice@example.com", attachments=None):
    return {
        "message_id": message_id,
        "thread_id": thread_id,
        "from_email": from_email,
        "subject": subject,
        "text_body": text_body,
        "html_body": "",
        "attachments": attachments or [],
        "created_at": "2026-05-14T15:31:00Z",
        "direction": "inbound",
        "is_spam": False, "spam_score": 0.0,
    }


@pytest.mark.asyncio
async def test_poll_once_skips_outbound_messages(email_config, in_queue):
    client = AsyncMock()
    client.list_messages.return_value = {
        "messages": [
            _msg("m1", ts="2026-05-14T15:31:00Z", direction="outbound"),
        ],
        "next_cursor": None,
    }
    ch, _ = _make_channel(in_queue, email_config, client=client)
    # Pretend startup ran:
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()
    assert in_queue.empty()
    client.get_message.assert_not_called()


@pytest.mark.asyncio
async def test_poll_once_outbound_does_not_advance_watermark(email_config, in_queue):
    """Outbound messages must not advance the watermark.

    Reproducer for the silent-drop bug: a scheduled outbound at T+1 used to
    push the watermark past an inbound at T whose delivery into the IMAP
    listing lagged by a few seconds. After the watermark jumped, the
    late-arriving inbound was permanently ≤ watermark and got dropped.
    """
    client = AsyncMock()
    client.list_messages.return_value = {
        "messages": [
            _msg("out1", ts="2026-05-14T15:32:00Z", direction="outbound",
                 from_email="bot@x.com"),
            _msg("in1", ts="2026-05-14T15:31:00Z"),
        ],
        "next_cursor": None,
    }
    client.get_message.side_effect = lambda mid: _detail(mid, text_body=f"body of {mid}")

    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()

    incoming = in_queue.get_nowait()
    assert incoming.reply_address["in_reply_to"] == "in1"
    # Watermark advances only to the inbound, NOT past it to the later outbound.
    assert ch.state.watermark_message_id == "in1"
    assert ch.state.watermark_created_at == datetime(2026, 5, 14, 15, 31, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_poll_once_drops_spam(email_config, in_queue):
    client = AsyncMock()
    client.list_messages.return_value = {
        "messages": [
            _msg("m1", ts="2026-05-14T15:31:00Z", is_spam=True),
            _msg("m2", ts="2026-05-14T15:32:00Z", spam_score=6.0),
        ],
        "next_cursor": None,
    }
    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()
    assert in_queue.empty()


@pytest.mark.asyncio
async def test_poll_once_drops_disallowed_sender(email_config, in_queue):
    client = AsyncMock()
    client.list_messages.return_value = {
        "messages": [
            _msg("m1", ts="2026-05-14T15:31:00Z", from_email="stranger@nope.com"),
        ],
        "next_cursor": None,
    }
    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()
    assert in_queue.empty()


@pytest.mark.parametrize("bad_sender", [
    "alice@example.com.evil.tld",      # suffix attack
    "evilalice@example.com.attacker.tld",  # prefix + suffix attack
    '"alice@example.com" <attacker@evil.tld>',  # display-name spoof
    "",  # malformed / empty From
    "not-an-email",
])
@pytest.mark.asyncio
async def test_poll_once_rejects_allowlist_bypass_attempts(
    email_config, in_queue, bad_sender,
):
    """Substring match → exact RFC-5322 address match. Bypass vectors must be rejected."""
    client = AsyncMock()
    client.list_messages.return_value = {
        "messages": [_msg("m1", ts="2026-05-14T15:31:00Z", from_email=bad_sender)],
        "next_cursor": None,
    }
    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()
    assert in_queue.empty()
    client.get_message.assert_not_called()


@pytest.mark.parametrize("good_sender", [
    "alice@example.com",
    "Alice@Example.COM",  # case-insensitive
    '"Alice" <alice@example.com>',  # display-name + address form
    "<alice@example.com>",
])
@pytest.mark.asyncio
async def test_poll_once_accepts_allowed_sender_variants(
    email_config, in_queue, good_sender,
):
    client = AsyncMock()
    client.list_messages.return_value = {
        "messages": [_msg("m1", ts="2026-05-14T15:31:00Z", from_email=good_sender)],
        "next_cursor": None,
    }
    client.get_message.side_effect = lambda mid: _detail(mid, from_email=good_sender)
    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()
    assert in_queue.qsize() == 1


@pytest.mark.asyncio
async def test_poll_once_queues_inbound_and_advances_watermark(email_config, in_queue):
    client = AsyncMock()
    client.list_messages.return_value = {
        "messages": [
            _msg("m2", ts="2026-05-14T15:32:00Z"),
            _msg("m1", ts="2026-05-14T15:31:00Z"),
        ],
        "next_cursor": None,
    }
    client.get_message.side_effect = lambda mid: _detail(mid, text_body=f"body of {mid}")

    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()

    # Two queued in chronological order.
    first = in_queue.get_nowait()
    second = in_queue.get_nowait()
    assert "body of m1" in first.content   # m1 (older) first
    assert "body of m2" in second.content  # m2 second
    assert first.session_id == "t1"
    assert first.channel == "email"
    assert first.reply_address["in_reply_to"] == "m1"
    assert first.reply_address["to"] == "alice@example.com"
    assert first.reply_address["subject"] == "Re: hi"
    # Watermark advanced to the newest message.
    assert ch.state.watermark_message_id == "m2"


@pytest.mark.asyncio
async def test_poll_once_walks_pages_until_watermark(email_config, in_queue):
    """Pagination terminates the moment we cross the watermark."""
    client = AsyncMock()
    # First page has m4, m3, m2; second has m1 (at watermark -- stop here).
    # page2 deliberately has next_cursor set so the watermark-stop logic must
    # fire; if it didn't, side_effect would raise StopIteration on a third fetch.
    page1 = {
        "messages": [
            _msg("m4", ts="2026-05-14T15:34:00Z"),
            _msg("m3", ts="2026-05-14T15:33:00Z"),
            _msg("m2", ts="2026-05-14T15:32:00Z"),
        ],
        "next_cursor": "cur-1",
    }
    page2 = {
        "messages": [
            _msg("m1", ts="2026-05-14T15:31:00Z"),  # at watermark -- stop here
        ],
        "next_cursor": "cur-2",  # not None: loop must stop via watermark, not cursor exhaustion
    }
    client.list_messages.side_effect = [page1, page2]
    client.get_message.side_effect = lambda mid: _detail(mid, text_body=f"body of {mid}")

    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 31, 0, tzinfo=timezone.utc), "m1")

    await ch._poll_once()

    queued = [in_queue.get_nowait() for _ in range(in_queue.qsize())]
    assert [m.reply_address["in_reply_to"] for m in queued] == ["m2", "m3", "m4"]
    # Walked exactly two pages; watermark stop prevented a third fetch.
    assert client.list_messages.call_count == 2


@pytest.mark.asyncio
async def test_poll_once_handles_api_returning_messages_out_of_order(email_config, in_queue):
    """Live IMAP listings do NOT guarantee strict newest-first ordering.

    Reproducer for the case where the watermark message (a previous outbound
    reply) is returned at index 0 even though a newer inbound message exists
    later in the same page. The poll must sort locally and not bail out on
    the first message ≤ watermark.
    """
    client = AsyncMock()
    # Watermark is the previous outbound reply (m_wm) at 06:05:24. The API
    # returns it first, then a NEWER inbound (m_new) at 06:16, then an older
    # inbound (m_old) at 06:05. Strict newest-first would have m_new at idx 0.
    client.list_messages.return_value = {
        "messages": [
            _msg("m_wm", ts="2026-05-15T06:05:24Z", direction="outbound",
                 from_email="bot@x.com"),
            _msg("m_new", ts="2026-05-15T06:16:41Z"),
            _msg("m_old", ts="2026-05-15T06:05:06Z"),
        ],
        "next_cursor": None,
    }
    client.get_message.side_effect = lambda mid: _detail(mid, text_body=f"body of {mid}")

    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(
        datetime(2026, 5, 15, 6, 5, 24, tzinfo=timezone.utc), "m_wm",
    )

    await ch._poll_once()

    queued = [in_queue.get_nowait() for _ in range(in_queue.qsize())]
    # m_new is the only message strictly after the watermark; m_wm matches it
    # exactly (skipped) and m_old is older (skipped).
    assert [m.reply_address["in_reply_to"] for m in queued] == ["m_new"]
    # Watermark advances to the newest message we saw.
    assert ch.state.watermark_message_id == "m_new"


@pytest.mark.asyncio
async def test_poll_once_does_not_advance_watermark_on_empty_batch(email_config, in_queue):
    client = AsyncMock()
    client.list_messages.return_value = {"messages": [], "next_cursor": None}

    ch, _ = _make_channel(in_queue, email_config, client=client)
    original = datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc)
    ch.state.set_watermark(original, "msg-old")

    await ch._poll_once()
    assert ch.state.watermark_created_at == original
    assert ch.state.watermark_message_id == "msg-old"


@pytest.mark.asyncio
async def test_poll_once_downloads_attachments(email_config, in_queue, tmp_path):
    client = AsyncMock()
    client.list_messages.return_value = {
        "messages": [_msg("m1", ts="2026-05-14T15:31:00Z")],
        "next_cursor": None,
    }
    client.get_message.return_value = _detail(
        "m1", thread_id="t1",
        attachments=[
            {"attachment_id": "a1", "filename": "report.pdf",
             "content_type": "application/pdf", "size": 1024},
        ],
    )
    async def fake_download(message_id, attachment_id, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"PDF")
    client.download_attachment.side_effect = fake_download

    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()
    incoming = in_queue.get_nowait()
    assert incoming.attachments is not None and len(incoming.attachments) == 1
    att = incoming.attachments[0]
    assert att["filename"] == "report.pdf"
    assert att["mime_type"] == "application/pdf"
    assert att["size"] == 3   # actual on-disk bytes
    assert Path(att["path"]).read_bytes() == b"PDF"
    # Body content lists the attachment.
    assert "report.pdf" in incoming.content


@pytest.mark.asyncio
async def test_poll_once_rejects_traversal_filename(email_config, in_queue, caplog, tmp_path):
    """Attacker-supplied path traversal filenames must not write outside the per-thread dir."""
    client = AsyncMock()
    client.list_messages.return_value = {
        "messages": [_msg("m1", ts="2026-05-14T15:31:00Z")],
        "next_cursor": None,
    }
    client.get_message.return_value = _detail(
        "m1", thread_id="t1",
        attachments=[
            {"attachment_id": "a1", "filename": "../../../etc/pwned.pdf",
             "content_type": "application/pdf", "size": 1024},
        ],
    )
    sentinel = tmp_path / "pwned_called_with"
    async def fake_download(message_id, attachment_id, dest):
        sentinel.write_text(str(dest))
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"PDF")
    client.download_attachment.side_effect = fake_download

    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    import logging
    with caplog.at_level(logging.WARNING):
        await ch._poll_once()

    incoming = in_queue.get_nowait()
    # Attachment is either skipped (manifest None) or basenamed to safe form.
    if incoming.attachments:
        for att in incoming.attachments:
            resolved = Path(att["path"]).resolve()
            out_root = Path(email_config.attachment_dir).resolve()
            assert str(resolved).startswith(str(out_root)), \
                f"attachment written outside {out_root}: {resolved}"
            assert "pwned.pdf" in att["filename"]  # basenamed
            assert ".." not in att["filename"]
    # download_attachment was either not called or only with paths under out_root.
    if sentinel.exists():
        called_dest = sentinel.read_text()
        out_root = str(Path(email_config.attachment_dir).resolve())
        assert called_dest.startswith(out_root)


@pytest.mark.asyncio
async def test_poll_once_basenames_filename_with_path_separators(email_config, in_queue, tmp_path):
    """filename='foo/bar.pdf' (legitimate MUA forwarding) gets basenamed to bar.pdf."""
    client = AsyncMock()
    client.list_messages.return_value = {
        "messages": [_msg("m1", ts="2026-05-14T15:31:00Z")],
        "next_cursor": None,
    }
    client.get_message.return_value = _detail(
        "m1", thread_id="t1",
        attachments=[
            {"attachment_id": "a1", "filename": "folder/report.pdf",
             "content_type": "application/pdf", "size": 1024},
        ],
    )
    async def fake_download(message_id, attachment_id, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"PDF")
    client.download_attachment.side_effect = fake_download

    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()
    incoming = in_queue.get_nowait()
    assert incoming.attachments is not None and len(incoming.attachments) == 1
    att = incoming.attachments[0]
    assert att["filename"] == "report.pdf"
    out_root = Path(email_config.attachment_dir).resolve() / "t1"
    assert Path(att["path"]).parent == out_root


@pytest.mark.parametrize("bad_name", ["", ".", "..", "...", "   "])
@pytest.mark.asyncio
async def test_poll_once_skips_empty_and_dot_filenames(email_config, in_queue, bad_name):
    client = AsyncMock()
    client.list_messages.return_value = {
        "messages": [_msg("m1", ts="2026-05-14T15:31:00Z")],
        "next_cursor": None,
    }
    client.get_message.return_value = _detail(
        "m1", thread_id="t1",
        attachments=[
            {"attachment_id": "a1", "filename": bad_name,
             "content_type": "application/pdf", "size": 1024},
        ],
    )
    client.download_attachment.side_effect = AssertionError(
        "download must not be called for rejected filename"
    )
    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()
    incoming = in_queue.get_nowait()
    assert incoming.attachments is None


@pytest.mark.asyncio
async def test_poll_once_strips_leading_dot_from_filename(email_config, in_queue):
    """`.bashrc`-style filenames have the leading dot stripped to prevent silent dotfile creation."""
    client = AsyncMock()
    client.list_messages.return_value = {
        "messages": [_msg("m1", ts="2026-05-14T15:31:00Z")],
        "next_cursor": None,
    }
    client.get_message.return_value = _detail(
        "m1", thread_id="t1",
        attachments=[
            {"attachment_id": "a1", "filename": ".bashrc",
             "content_type": "text/plain", "size": 10},
        ],
    )
    written: dict = {}
    async def fake_download(message_id, attachment_id, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"hello")
        written["dest"] = dest
    client.download_attachment.side_effect = fake_download

    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()
    incoming = in_queue.get_nowait()
    assert incoming.attachments is not None and len(incoming.attachments) == 1
    att = incoming.attachments[0]
    # Leading dot stripped: no hidden dotfile gets planted.
    assert not att["filename"].startswith(".")
    assert att["filename"] == "bashrc"


@pytest.mark.asyncio
async def test_poll_once_skips_failed_attachment_but_keeps_message(email_config, in_queue):
    client = AsyncMock()
    client.list_messages.return_value = {
        "messages": [_msg("m1", ts="2026-05-14T15:31:00Z")],
        "next_cursor": None,
    }
    client.get_message.return_value = _detail("m1", attachments=[
        {"attachment_id": "a1", "filename": "broken.pdf",
         "content_type": "application/pdf", "size": 1024},
    ])
    client.download_attachment.side_effect = FastmailError("expired URL")

    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()
    incoming = in_queue.get_nowait()
    assert incoming.attachments is None  # download failed → no manifest entry


from src.channels.base import OutgoingMessage


def _outgoing(content, *, reply_address, attachments=None, final=True):
    return OutgoingMessage(
        content=content,
        channel="email",
        session_id="t1",
        reply_address=reply_address,
        attachments=attachments,
        final=final,
    )


@pytest.mark.asyncio
async def test_send_skips_streaming_deltas(email_config, in_queue):
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    msg = _outgoing("partial", reply_address={"to": "a@x.com", "subject": "re", "in_reply_to": "m1"}, final=False)
    await ch.send(msg)
    client.send_reply.assert_not_called()
    client.send_with_attachments.assert_not_called()


@pytest.mark.asyncio
async def test_send_uses_reply_endpoint_when_no_attachments(email_config, in_queue):
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    msg = _outgoing(
        "# Hi back",
        reply_address={"to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"},
    )
    await ch.send(msg)
    client.send_reply.assert_awaited_once()
    kwargs = client.send_reply.await_args.kwargs
    assert kwargs["in_reply_to"] == "m1"
    assert kwargs["to"] == "alice@example.com"
    assert kwargs["subject"] == "Re: hi"
    assert kwargs["text_body"] == "# Hi back"
    assert "<h1>Hi back</h1>" in kwargs["html_body"]
    client.send_with_attachments.assert_not_called()


@pytest.mark.asyncio
async def test_send_uses_messages_endpoint_when_attachments_present(email_config, in_queue, tmp_path):
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    f = tmp_path / "doc.txt"
    f.write_bytes(b"x")
    msg = _outgoing(
        "# see attached",
        reply_address={"to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"},
        attachments=[{"filename": "doc.txt", "path": str(f), "mime_type": "text/plain", "size": 1}],
    )
    await ch.send(msg)
    client.send_with_attachments.assert_awaited_once()
    kwargs = client.send_with_attachments.await_args.kwargs
    assert kwargs["in_reply_to"] == "m1"
    assert kwargs["to"] == "alice@example.com"
    assert kwargs["subject"] == "Re: hi"
    assert kwargs["text_body"] == "# see attached"
    assert kwargs["attachment_paths"] == [str(f)]
    assert "<h1>see attached</h1>" in kwargs["html_body"]
    client.send_reply.assert_not_called()


@pytest.mark.asyncio
async def test_send_passes_rendered_html_body_with_bold_and_link(email_config, in_queue):
    """Reply with **bold** + link round-trips through markdown rendering."""
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    msg = _outgoing(
        "this is **bold** and a [link](https://example.com)",
        reply_address={"to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"},
    )
    await ch.send(msg)
    kwargs = client.send_reply.await_args.kwargs
    assert "<strong>bold</strong>" in kwargs["html_body"]
    assert '<a href="https://example.com">link</a>' in kwargs["html_body"]


@pytest.mark.asyncio
async def test_send_preserves_raw_markdown_in_text_body(email_config, in_queue):
    """text_body is the raw markdown — plain-text fallback for clients that don't render HTML."""
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    raw = "# Heading\n\n- item one\n- item two\n\n**bold** here"
    msg = _outgoing(
        raw,
        reply_address={"to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"},
    )
    await ch.send(msg)
    assert client.send_reply.await_args.kwargs["text_body"] == raw


def test_render_html_no_raw_markdown_survives():
    """Rich markdown sample renders to real HTML — no raw syntax leaks through."""
    from src.channels.email import _render_html

    sample = (
        "# Top heading\n\n"
        "## Sub heading\n\n"
        "Some **bold** and *italic* and a [link](https://example.com).\n\n"
        "- bullet one\n"
        "- bullet two\n\n"
        "Inline `code` here.\n\n"
        "```\nfenced block\n```\n"
    )
    html = _render_html(sample)

    # Expected HTML tags
    assert "<h1>" in html and "Top heading" in html
    assert "<h2>" in html and "Sub heading" in html
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert '<a href="https://example.com">link</a>' in html
    assert "<ul>" in html
    assert "<li>bullet one</li>" in html
    assert "<code>code</code>" in html
    assert "<pre>" in html and "fenced block" in html

    # Raw markdown syntax must not survive verbatim into the rendered body
    # (extract just the rendered body, excluding the wrapper's <style> CSS)
    body_start = html.rfind("<body>")
    body_end = html.rfind("</body>")
    body = html[body_start:body_end] if body_start >= 0 else html
    assert "**" not in body
    assert "__" not in body
    assert "[link](" not in body
    assert "```" not in body
    # No bare leading "## " markdown header markers
    assert "## " not in body
    assert "# Top" not in body


@pytest.mark.asyncio
async def test_send_logs_and_returns_on_fastmail_error(email_config, in_queue, caplog):
    client = AsyncMock()
    client.send_reply.side_effect = FastmailError("rate limited")
    ch, _ = _make_channel(in_queue, email_config, client=client)
    msg = _outgoing(
        "hi",
        reply_address={"to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"},
    )
    await ch.send(msg)  # does not raise


# --- Outbound retry / dead-letter ledger ---------------------------------

from src.channels._email_state import EmailState

_UTC = timezone.utc


async def _poll_one_inbound(ch, client, mid="m1", ts="2026-05-14T15:31:00Z"):
    """Drive one poll that enqueues a single inbound and records it in the ledger."""
    client.list_messages.return_value = {
        "messages": [_msg(mid, ts=ts)], "next_cursor": None,
    }
    client.get_message.side_effect = lambda m: _detail(m)
    ch.state.set_cursor(datetime(2026, 5, 14, 15, 0, 0, tzinfo=_UTC), "")
    await ch._poll_once()
    incoming = in_queue_drain(ch)
    return incoming


def in_queue_drain(ch):
    items = []
    while not ch.in_queue.empty():
        items.append(ch.in_queue.get_nowait())
    return items


@pytest.mark.asyncio
async def test_poll_records_pending_before_enqueue(email_config, in_queue):
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    await _poll_one_inbound(ch, client, mid="m1")
    assert "m1" in ch.state.pending
    pr = ch.state.pending["m1"]
    assert pr.status == "queued"
    assert pr.reply_address["in_reply_to"] == "m1"


@pytest.mark.asyncio
async def test_send_success_acks_ledger_entry(email_config, in_queue):
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    await _poll_one_inbound(ch, client, mid="m1")
    assert "m1" in ch.state.pending

    msg = _outgoing("answer", reply_address={
        "to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"})
    await ch.send(msg)

    client.send_reply.assert_awaited_once()
    assert "m1" not in ch.state.pending  # acked


@pytest.mark.asyncio
async def test_send_failure_records_retry_not_dropped(email_config, in_queue):
    """Acceptance criterion 3: a failed send must NOT silently drop the message."""
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    await _poll_one_inbound(ch, client, mid="m1")

    client.send_reply.side_effect = FastmailError("dns outage")
    msg = _outgoing("the answer", reply_address={
        "to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"})
    await ch.send(msg)

    # Recoverable: still in the ledger, marked for retry, payload stored.
    assert "m1" in ch.state.pending
    pr = ch.state.pending["m1"]
    assert pr.status == "retry"
    assert pr.reply_payload["text_body"] == "the answer"


@pytest.mark.asyncio
async def test_failed_send_then_drain_resends_and_clears(email_config, in_queue):
    """After a transient outage, the drain loop re-sends the stored reply."""
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    await _poll_one_inbound(ch, client, mid="m1")

    client.send_reply.side_effect = FastmailError("outage")
    msg = _outgoing("the answer", reply_address={
        "to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"})
    await ch.send(msg)
    assert ch.state.pending["m1"].status == "retry"

    # Outage clears.
    client.send_reply.side_effect = None
    client.send_reply.reset_mock()
    await ch._drain_retries()

    client.send_reply.assert_awaited_once()
    kwargs = client.send_reply.await_args.kwargs
    assert kwargs["in_reply_to"] == "m1"
    assert kwargs["text_body"] == "the answer"
    assert "m1" not in ch.state.pending  # acked after successful re-send


@pytest.mark.asyncio
async def test_failed_send_not_reenqueued_by_later_poll(email_config, in_queue):
    """Criterion 2: an unanswered message is recoverable, not re-enqueued."""
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    await _poll_one_inbound(ch, client, mid="m1")
    client.send_reply.side_effect = FastmailError("outage")
    await ch.send(_outgoing("answer", reply_address={
        "to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"}))
    assert "m1" in ch.state.pending

    # Same message reappears in a later listing (cursor already past it).
    client.list_messages.return_value = {
        "messages": [_msg("m1", ts="2026-05-14T15:31:00Z")], "next_cursor": None}
    await ch._poll_once()
    assert in_queue_drain(ch) == []  # not re-enqueued


@pytest.mark.asyncio
async def test_poll_ledger_dedup_skips_pending_but_advances_cursor(email_config, in_queue):
    """A message already in the ledger is not re-enqueued, but the cursor still advances."""
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    # Pre-seed a pending entry and put the cursor behind it.
    ch.state.set_cursor(datetime(2026, 5, 14, 15, 0, 0, tzinfo=_UTC), "")
    ch.state.add_pending(
        "m1", created_at=datetime(2026, 5, 14, 15, 31, 0, tzinfo=_UTC),
        thread_id="t1",
        reply_address={"to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"})

    client.list_messages.return_value = {
        "messages": [_msg("m1", ts="2026-05-14T15:31:00Z")], "next_cursor": None}
    await ch._poll_once()

    assert in_queue_drain(ch) == []  # ledger dedup: not re-enqueued
    assert ch.state.cursor_message_id == "m1"  # cursor advanced past it


@pytest.mark.asyncio
async def test_dead_letter_after_max_retries(email_config, in_queue, caplog):
    import logging
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.config.send_max_retries = 2
    ch._escalate_hook = MagicMock()
    await _poll_one_inbound(ch, client, mid="m1")

    client.send_reply.side_effect = FastmailError("persistent outage")
    await ch.send(_outgoing("answer", reply_address={
        "to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"}))
    assert ch.state.pending["m1"].status == "retry"

    with caplog.at_level(logging.ERROR):
        await ch._drain_retries()  # 2nd attempt -> dead-letter

    assert ch.state.pending["m1"].status == "dead"
    ch._escalate_hook.assert_called_once()


@pytest.mark.asyncio
async def test_escalation_fires_once_at_threshold(email_config, in_queue):
    client = AsyncMock()
    client.send_reply.side_effect = FastmailError("down")
    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.config.failure_alert_threshold = 3
    ch._escalate_hook = MagicMock()

    for i in range(5):
        await ch.send(_outgoing("a", reply_address={
            "to": "alice@example.com", "subject": "re", "in_reply_to": f"x{i}"}))

    ch._escalate_hook.assert_called_once()


@pytest.mark.asyncio
async def test_send_success_resets_failure_counter(email_config, in_queue):
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.config.failure_alert_threshold = 3
    ch._escalate_hook = MagicMock()

    client.send_reply.side_effect = FastmailError("down")
    for i in range(2):
        await ch.send(_outgoing("a", reply_address={
            "to": "alice@example.com", "subject": "re", "in_reply_to": f"x{i}"}))
    # A success resets the consecutive counter so the threshold is never hit.
    client.send_reply.side_effect = None
    await ch.send(_outgoing("ok", reply_address={
        "to": "alice@example.com", "subject": "re", "in_reply_to": "x-ok"}))
    client.send_reply.side_effect = FastmailError("down")
    for i in range(2):
        await ch.send(_outgoing("a", reply_address={
            "to": "alice@example.com", "subject": "re", "in_reply_to": f"y{i}"}))

    ch._escalate_hook.assert_not_called()


# --- Startup boot-state classifier + ledger re-drive ---------------------

async def _run_start_briefly(ch, sleep=0.05):
    task = asyncio.create_task(ch.start())
    await asyncio.sleep(sleep)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_start_corrupt_state_does_not_fast_forward(email_config, in_queue, caplog):
    import logging
    # Corrupt the state file before the channel loads it.
    email_config.state_file.parent.mkdir(parents=True, exist_ok=True)
    email_config.state_file.write_text("{ this is not json")

    client = AsyncMock()
    client.validate_inbox.return_value = {"data": {"email": "bot@x"}}
    ch, _ = _make_channel(in_queue, email_config, client=client)
    assert ch.state.corrupt is True
    ch._escalate_hook = MagicMock()

    with caplog.at_level(logging.ERROR):
        await ch.start()  # returns without entering the poll loop

    client.list_messages.assert_not_called()      # did NOT poll
    assert ch.state.cursor_created_at is None      # did NOT fast-forward
    ch._escalate_hook.assert_called_once()
    assert any("corrupt" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_start_first_run_fast_forwards(email_config, in_queue):
    """A genuine first run (no state file) is allowed to skip pre-existing mail."""
    client = AsyncMock()
    client.validate_inbox.return_value = {"data": {"email": "bot@x"}}
    client.list_messages.return_value = {"messages": [], "next_cursor": None}
    ch, _ = _make_channel(in_queue, email_config, client=client)
    assert ch.state.corrupt is False

    await _run_start_briefly(ch)

    assert ch.state.cursor_created_at is not None  # fast-forwarded to now


@pytest.mark.asyncio
async def test_start_redrives_queued_entry(email_config, in_queue):
    """A queued ledger entry (agent crashed pre-reply) is re-enqueued on boot."""
    seed = EmailState.load(email_config.state_file)
    seed.set_cursor(datetime(2026, 5, 14, 15, 0, 0, tzinfo=_UTC), "c0")
    seed.add_pending(
        "m1", created_at=datetime(2026, 5, 14, 15, 31, 0, tzinfo=_UTC),
        thread_id="t1",
        reply_address={"to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"})
    seed.save()

    client = AsyncMock()
    client.validate_inbox.return_value = {"data": {"email": "bot@x"}}
    client.list_messages.return_value = {"messages": [], "next_cursor": None}
    client.get_message.side_effect = lambda m: _detail(m)
    ch, _ = _make_channel(in_queue, email_config, client=client)

    await _run_start_briefly(ch)

    items = in_queue_drain(ch)
    assert any(i.reply_address["in_reply_to"] == "m1" for i in items)


@pytest.mark.asyncio
async def test_start_redrives_retry_entry_resends_stored_reply(email_config, in_queue):
    """A retry ledger entry (send crashed) re-sends its stored reply on boot."""
    seed = EmailState.load(email_config.state_file)
    seed.set_cursor(datetime(2026, 5, 14, 15, 0, 0, tzinfo=_UTC), "c0")
    seed.add_pending(
        "m2", created_at=datetime(2026, 5, 14, 15, 31, 0, tzinfo=_UTC),
        thread_id="t1",
        reply_address={"to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m2"})
    seed.mark_retry(
        "m2",
        reply_payload={"to": "alice@example.com", "subject": "Re: hi",
                       "text_body": "stored reply", "html_body": None, "attachment_paths": []},
        next_retry_at=datetime(2026, 5, 14, 15, 0, 0, tzinfo=_UTC))  # due in the past
    seed.save()

    client = AsyncMock()
    client.validate_inbox.return_value = {"data": {"email": "bot@x"}}
    client.list_messages.return_value = {"messages": [], "next_cursor": None}
    ch, _ = _make_channel(in_queue, email_config, client=client)

    await _run_start_briefly(ch)

    client.send_reply.assert_awaited()
    kwargs = client.send_reply.await_args.kwargs
    assert kwargs["in_reply_to"] == "m2"
    assert kwargs["text_body"] == "stored reply"
    assert "m2" not in ch.state.pending  # acked


@pytest.mark.asyncio
async def test_poll_get_message_failure_does_not_advance_cursor(email_config, in_queue):
    """A transient detail-fetch failure must NOT silently drop the inbound.

    Reproducer for the second silent-drop path: the cursor must not advance
    past a message whose get_message() raised, so it is retried next poll.
    """
    client = AsyncMock()
    client.list_messages.return_value = {
        "messages": [_msg("m1", ts="2026-05-14T15:31:00Z")], "next_cursor": None}
    client.get_message.side_effect = FastmailError("transient 503")

    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_cursor(datetime(2026, 5, 14, 15, 0, 0, tzinfo=_UTC), "")

    await ch._poll_once()

    assert in_queue_drain(ch) == []
    assert "m1" not in ch.state.pending  # not falsely tracked
    # Cursor NOT advanced past m1 — still after the cursor, so it is re-polled.
    assert ch.state.is_after_cursor(datetime(2026, 5, 14, 15, 31, 0, tzinfo=_UTC), "m1")


@pytest.mark.asyncio
async def test_poll_transient_failure_pins_cursor_but_processes_newer(email_config, in_queue):
    """An older message's transient failure pins the cursor, yet a newer
    message still gets enqueued (and is not lost: cursor stays behind both)."""
    client = AsyncMock()
    client.list_messages.return_value = {
        "messages": [
            _msg("m_new", ts="2026-05-14T15:35:00Z"),
            _msg("m_old", ts="2026-05-14T15:31:00Z"),
        ],
        "next_cursor": None,
    }

    def _get(mid):
        if mid == "m_old":
            raise FastmailError("503")
        return _detail(mid)
    client.get_message.side_effect = _get

    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_cursor(datetime(2026, 5, 14, 15, 0, 0, tzinfo=_UTC), "")

    await ch._poll_once()

    items = in_queue_drain(ch)
    assert [i.reply_address["in_reply_to"] for i in items] == ["m_new"]
    # Cursor pinned behind the failed older message so it is retried.
    assert ch.state.is_after_cursor(datetime(2026, 5, 14, 15, 31, 0, tzinfo=_UTC), "m_old")
