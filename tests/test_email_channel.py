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


from src.channels.base import IncomingMessage


@pytest.mark.asyncio
async def test_poll_once_pushes_message(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)

    thread = {
        "id": "thread_1",
        "messages": [
            {
                "id": "msg_1",
                "from": "alice@example.com",
                "subject": "Hello",
                "body": "Hi there!",
                "attachments": [],
            }
        ],
    }

    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.search.return_value = [{"id": "thread_1"}]
        mock_gog.thread_get.return_value = thread
        await ch._poll_once()

    msg = in_queue.get_nowait()
    assert msg.content == "Hi there!"
    assert msg.channel == "email"
    assert msg.session_id == "thread_1"
    assert msg.reply_address == {
        "to": "alice@example.com",
        "subject": "Re: Hello",
        "in_reply_to": "msg_1",
    }
    assert msg.attachments is None
    assert ch.last_seen["thread_1"] == "msg_1"


@pytest.mark.asyncio
async def test_poll_once_filters_disallowed_sender(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)

    thread = {
        "id": "thread_1",
        "messages": [
            {
                "id": "msg_1",
                "from": "stranger@example.com",
                "subject": "Spam",
                "body": "Buy stuff!",
                "attachments": [],
            }
        ],
    }

    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.search.return_value = [{"id": "thread_1"}]
        mock_gog.thread_get.return_value = thread
        await ch._poll_once()

    assert in_queue.empty()
    assert ch.last_seen["thread_1"] == "msg_1"


@pytest.mark.asyncio
async def test_poll_once_skips_already_seen_messages(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)
    ch.last_seen["thread_1"] = "msg_1"

    thread = {
        "id": "thread_1",
        "messages": [
            {
                "id": "msg_1",
                "from": "alice@example.com",
                "subject": "Hello",
                "body": "Hi there!",
                "attachments": [],
            },
            {
                "id": "msg_2",
                "from": "alice@example.com",
                "subject": "Re: Hello",
                "body": "Follow up!",
                "attachments": [],
            },
        ],
    }

    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.search.return_value = [{"id": "thread_1"}]
        mock_gog.thread_get.return_value = thread
        await ch._poll_once()

    msg = in_queue.get_nowait()
    assert msg.content == "Follow up!"
    assert msg.reply_address["in_reply_to"] == "msg_2"
    assert in_queue.empty()
    assert ch.last_seen["thread_1"] == "msg_2"


@pytest.mark.asyncio
async def test_poll_once_accepts_all_when_no_allowlist(in_queue):
    config = EmailChannelConfig(enabled=True, account="bot@example.com", poll_interval_sec=1)
    ch = EmailChannel(in_queue, config)

    thread = {
        "id": "thread_1",
        "messages": [
            {
                "id": "msg_1",
                "from": "anyone@example.com",
                "subject": "Hello",
                "body": "Hi!",
                "attachments": [],
            }
        ],
    }

    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.search.return_value = [{"id": "thread_1"}]
        mock_gog.thread_get.return_value = thread
        await ch._poll_once()

    assert not in_queue.empty()


@pytest.mark.asyncio
async def test_poll_once_no_double_re_prefix(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)

    thread = {
        "id": "thread_1",
        "messages": [
            {
                "id": "msg_1",
                "from": "alice@example.com",
                "subject": "Re: Hello",
                "body": "Reply!",
                "attachments": [],
            }
        ],
    }

    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.search.return_value = [{"id": "thread_1"}]
        mock_gog.thread_get.return_value = thread
        await ch._poll_once()

    msg = in_queue.get_nowait()
    assert msg.reply_address["subject"] == "Re: Hello"


@pytest.mark.asyncio
async def test_poll_once_with_attachments(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)

    thread = {
        "id": "thread_1",
        "messages": [
            {
                "id": "msg_1",
                "from": "alice@example.com",
                "subject": "Report",
                "body": "See attached.",
                "attachments": [
                    {"filename": "report.pdf", "mimeType": "application/pdf", "size": 12288},
                ],
            }
        ],
    }

    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.search.return_value = [{"id": "thread_1"}]
        mock_gog.thread_get.return_value = thread
        mock_gog.thread_download_attachments.return_value = None
        await ch._poll_once()

    msg = in_queue.get_nowait()
    assert msg.attachments is not None
    assert len(msg.attachments) == 1
    assert msg.attachments[0]["filename"] == "report.pdf"
    assert msg.attachments[0]["path"] == "/tmp/attachments/thread_1/report.pdf"
    assert msg.attachments[0]["mime_type"] == "application/pdf"
    assert msg.attachments[0]["size"] == 12288
    assert "report.pdf" in msg.content
    assert "12KB" in msg.content


@pytest.mark.asyncio
async def test_poll_once_continues_on_thread_error(email_config, in_queue):
    """If one thread fails to fetch, other threads still get processed."""
    ch = EmailChannel(in_queue, email_config)

    good_thread = {
        "id": "thread_2",
        "messages": [
            {"id": "msg_2", "from": "alice@example.com", "subject": "OK", "body": "Works!", "attachments": []},
        ],
    }

    from src.channels.gog import GogError

    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.GogError = GogError
        mock_gog.search.return_value = [{"id": "thread_1"}, {"id": "thread_2"}]
        mock_gog.thread_get.side_effect = [GogError("network error"), good_thread]
        await ch._poll_once()

    msg = in_queue.get_nowait()
    assert msg.content == "Works!"


from src.channels.base import OutgoingMessage


@pytest.mark.asyncio
async def test_send_reply_and_label(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)

    msg = OutgoingMessage(
        content="Got it, thanks!",
        channel="email",
        session_id="thread_1",
        reply_address={
            "to": "alice@example.com",
            "subject": "Re: Hello",
            "in_reply_to": "msg_1",
        },
    )

    with patch("src.channels.email.gog") as mock_gog:
        await ch.send(msg)

    mock_gog.send_reply.assert_called_once_with(
        to="alice@example.com",
        subject="Re: Hello",
        body="Got it, thanks!",
        reply_to_message_id="msg_1",
        account="bot@example.com",
    )
    mock_gog.thread_modify.assert_called_once_with(
        "thread_1", add_label="agent/processed", account="bot@example.com",
    )


@pytest.mark.asyncio
async def test_send_failure_does_not_label(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)

    msg = OutgoingMessage(
        content="Reply text",
        channel="email",
        session_id="thread_1",
        reply_address={
            "to": "alice@example.com",
            "subject": "Re: Hello",
            "in_reply_to": "msg_1",
        },
    )

    from src.channels.gog import GogError

    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.GogError = GogError
        mock_gog.send_reply.side_effect = GogError("send failed")
        await ch.send(msg)  # should not raise

    mock_gog.thread_modify.assert_not_called()
