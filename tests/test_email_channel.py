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
