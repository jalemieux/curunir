import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from src.channels.email import EmailChannel
from src.config import EmailChannelConfig


@pytest.fixture
def email_config():
    return EmailChannelConfig(
        enabled=True,
        account="bot@example.com",
        poll_interval_sec=1,
        allowed_senders=["alice@example.com"],
        processed_label="agent/processed",
        attachment_dir="/tmp/attachments",
    )


@pytest.fixture
def in_queue():
    return asyncio.Queue()


def test_constructor(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)
    assert ch.in_queue is in_queue
    assert ch.config is email_config
    assert ch.account == "bot@example.com"
    assert ch.poll_interval == 1
    assert ch.allowed_senders == ["alice@example.com"]
    assert ch.processed_label == "agent/processed"
    assert ch.attachment_dir == "/tmp/attachments"
    assert ch.last_seen == {}


@pytest.mark.asyncio
async def test_ensure_label_exists_already(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)
    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.labels_list.return_value = [{"name": "agent/processed"}]
        await ch._ensure_label()
    mock_gog.labels_list.assert_called_once_with("bot@example.com")
    mock_gog.labels_create.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_label_creates_missing(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)
    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.labels_list.return_value = [{"name": "INBOX"}]
        await ch._ensure_label()
    mock_gog.labels_create.assert_called_once_with("agent/processed", "bot@example.com")
