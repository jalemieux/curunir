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
    client.list_messages.return_value = {"data": [], "next_cursor": None}

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
        "data": [
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
async def test_poll_once_drops_spam(email_config, in_queue):
    client = AsyncMock()
    client.list_messages.return_value = {
        "data": [
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
        "data": [
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
        "data": [
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
    # First page has m4, m3, m2; second would have m1 (already seen).
    page1 = {
        "data": [
            _msg("m4", ts="2026-05-14T15:34:00Z"),
            _msg("m3", ts="2026-05-14T15:33:00Z"),
            _msg("m2", ts="2026-05-14T15:32:00Z"),
        ],
        "next_cursor": "cur-1",
    }
    page2 = {
        "data": [
            _msg("m1", ts="2026-05-14T15:31:00Z"),  # at watermark -- stop here
        ],
        "next_cursor": None,
    }
    client.list_messages.side_effect = [page1, page2]
    client.get_message.side_effect = lambda mid: _detail(mid, text_body=f"body of {mid}")

    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 31, 0, tzinfo=timezone.utc), "m1")

    await ch._poll_once()

    queued = [in_queue.get_nowait() for _ in range(in_queue.qsize())]
    assert [m.reply_address["in_reply_to"] for m in queued] == ["m2", "m3", "m4"]
    # Walked two pages.
    assert client.list_messages.call_count == 2


@pytest.mark.asyncio
async def test_poll_once_does_not_advance_watermark_on_empty_batch(email_config, in_queue):
    client = AsyncMock()
    client.list_messages.return_value = {"data": [], "next_cursor": None}

    ch, _ = _make_channel(in_queue, email_config, client=client)
    original = datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc)
    ch.state.set_watermark(original, "msg-old")

    await ch._poll_once()
    assert ch.state.watermark_created_at == original
    assert ch.state.watermark_message_id == "msg-old"
