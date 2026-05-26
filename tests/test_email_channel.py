import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.email import EmailChannel
from src.channels.deadsimple import DeadsimpleError
from src.config import EmailChannelConfig


@pytest.fixture
def email_config(tmp_path):
    return EmailChannelConfig(
        enabled=True,
        api_key="dse_test",
        inbox_id="inbox-uuid-1",
        api_base="https://api.deadsimple.email",
        poll_interval_sec=1,
        allowed_senders=["alice@example.com"],
        restrict_outbound=True,
        attachment_dir=str(tmp_path / "attachments"),
        state_file=tmp_path / "email_state.json",
        spam_score_threshold=5.0,
    )


@pytest.fixture
def in_queue():
    return asyncio.Queue()


def _make_channel(in_queue, config, client: AsyncMock | None = None):
    """Construct the channel with the deadsimple client patched out."""
    mock_client = client or AsyncMock()
    with patch("src.channels.email.DeadsimpleClient", return_value=mock_client):
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
    client.validate_inbox.return_value = {"data": {"inbox_id": "inbox-uuid-1", "email": "bot@deadsimple.email"}}
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
    client.validate_inbox.side_effect = DeadsimpleError("404: inbox not found")

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
    push the watermark past an inbound at T whose delivery into the deadsimple
    list endpoint lagged by a few seconds. After the watermark jumped, the
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
    """Live deadsimple API does NOT guarantee strict newest-first ordering.

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
    client.download_attachment.side_effect = DeadsimpleError("expired URL")

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
        "Hi back",
        reply_address={"to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"},
    )
    await ch.send(msg)
    client.send_reply.assert_awaited_once_with(
        in_reply_to="m1", to="alice@example.com", text_body="Hi back"
    )
    client.send_with_attachments.assert_not_called()


@pytest.mark.asyncio
async def test_send_uses_messages_endpoint_when_attachments_present(email_config, in_queue, tmp_path):
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    f = tmp_path / "doc.txt"
    f.write_bytes(b"x")
    msg = _outgoing(
        "see attached",
        reply_address={"to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"},
        attachments=[{"filename": "doc.txt", "path": str(f), "mime_type": "text/plain", "size": 1}],
    )
    await ch.send(msg)
    client.send_with_attachments.assert_awaited_once_with(
        in_reply_to="m1",
        to="alice@example.com",
        subject="Re: hi",
        text_body="see attached",
        attachment_paths=[str(f)],
    )
    client.send_reply.assert_not_called()


@pytest.mark.asyncio
async def test_send_logs_and_returns_on_deadsimple_error(email_config, in_queue, caplog):
    client = AsyncMock()
    client.send_reply.side_effect = DeadsimpleError("rate limited")
    ch, _ = _make_channel(in_queue, email_config, client=client)
    msg = _outgoing(
        "hi",
        reply_address={"to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"},
    )
    await ch.send(msg)  # does not raise
