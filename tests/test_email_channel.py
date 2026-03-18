import asyncio
import os
import tempfile
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
async def test_poll_once_with_attachments(in_queue):
    with tempfile.TemporaryDirectory() as tmpdir:
        config = EmailChannelConfig(
            account="bot@example.com",
            allowed_senders=["alice@example.com"],
            attachment_dir=tmpdir,
        )
        ch = EmailChannel(in_queue, config)

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

        def fake_download(thread_id, out_dir, account):
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "report.pdf"), "wb") as f:
                f.write(b"fake pdf")

        with patch("src.channels.email.gog") as mock_gog:
            mock_gog.search.return_value = [{"id": "thread_1"}]
            mock_gog.thread_get.return_value = thread
            mock_gog.thread_download_attachments.side_effect = fake_download
            await ch._poll_once()

        msg = in_queue.get_nowait()
        assert msg.attachments is not None
        assert len(msg.attachments) == 1
        assert msg.attachments[0]["filename"] == "report.pdf"
        assert msg.attachments[0]["path"].endswith("/thread_1/report.pdf")
        assert msg.attachments[0]["mime_type"] == "application/pdf"
        assert msg.attachments[0]["size"] == 12288
        assert "report.pdf" in msg.content
        assert "12KB" in msg.content
        # Verify the file is actually openable at the manifest path
        with open(msg.attachments[0]["path"], "rb") as f:
            assert f.read() == b"fake pdf"


@pytest.mark.asyncio
async def test_poll_once_with_prefixed_attachments(in_queue):
    """gog saves files with an attachment-ID prefix; manifest should resolve them."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = EmailChannelConfig(
            account="bot@example.com",
            allowed_senders=["alice@example.com"],
            attachment_dir=tmpdir,
        )
        ch = EmailChannel(in_queue, config)

        thread = {
            "id": "thread_1",
            "messages": [
                {
                    "id": "msg_1",
                    "from": "alice@example.com",
                    "subject": "Screenshot",
                    "body": "See attached.",
                    "attachments": [
                        {"filename": "screenshot.png", "mimeType": "image/png", "size": 4096},
                    ],
                }
            ],
        }

        prefixed_name = "19d00de117c89d79_ANGjdJ9P_screenshot.png"
        def fake_download(thread_id, out_dir, account):
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, prefixed_name), "wb") as f:
                f.write(b"fake png")

        with patch("src.channels.email.gog") as mock_gog:
            mock_gog.search.return_value = [{"id": "thread_1"}]
            mock_gog.thread_get.return_value = thread
            mock_gog.thread_download_attachments.side_effect = fake_download
            await ch._poll_once()

        msg = in_queue.get_nowait()
        assert msg.attachments is not None
        assert len(msg.attachments) == 1
        assert msg.attachments[0]["filename"] == "screenshot.png"
        assert prefixed_name in msg.attachments[0]["path"]
        with open(msg.attachments[0]["path"], "rb") as f:
            assert f.read() == b"fake png"


@pytest.mark.asyncio
async def test_attachment_manifest_uses_real_filesystem(in_queue):
    """End-to-end test: gog creates prefixed files on disk, manifest resolves them,
    and the file is actually openable at the manifest path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = EmailChannelConfig(
            account="bot@example.com",
            allowed_senders=["alice@example.com"],
            attachment_dir=tmpdir,
        )
        ch = EmailChannel(in_queue, config)

        thread = {
            "id": "thread_1",
            "messages": [
                {
                    "id": "msg_1",
                    "from": "alice@example.com",
                    "subject": "Screenshot",
                    "body": "See attached.",
                    "attachments": [
                        {"filename": "Screenshot 2026-03-18 at 5.13.10 AM.png",
                         "mimeType": "image/png", "size": 4096},
                    ],
                }
            ],
        }

        # Simulate what gog does: create the prefixed file on disk
        prefixed_name = "19d011139876056b_ANGjdJ83_Screenshot 2026-03-18 at 5.13.10 AM.png"
        def fake_download(thread_id, out_dir, account):
            os.makedirs(out_dir, exist_ok=True)
            filepath = os.path.join(out_dir, prefixed_name)
            with open(filepath, "wb") as f:
                f.write(b"\x89PNG fake image data")

        with patch("src.channels.email.gog") as mock_gog:
            mock_gog.search.return_value = [{"id": "thread_1"}]
            mock_gog.thread_get.return_value = thread
            mock_gog.thread_download_attachments.side_effect = fake_download
            await ch._poll_once()

        msg = in_queue.get_nowait()
        assert msg.attachments is not None
        assert len(msg.attachments) == 1

        att = msg.attachments[0]
        assert att["filename"] == "Screenshot 2026-03-18 at 5.13.10 AM.png"
        assert prefixed_name in att["path"]

        # THE CRITICAL CHECK: the file must actually be openable at this path
        with open(att["path"], "rb") as f:
            data = f.read()
        assert data == b"\x89PNG fake image data"

        # Verify the path is in the message content (what the agent sees)
        assert att["path"] in msg.content


@pytest.mark.asyncio
async def test_attachment_unicode_whitespace_normalized(in_queue):
    """Gmail uses \\u202f (narrow no-break space) in filenames like
    'Screenshot 2026-03-18 at 5.13.10\\u202fAM.png'. LLMs convert this
    to a regular space in tool calls, causing file-not-found. Verify
    we normalize the filename on disk so the path always uses regular spaces."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = EmailChannelConfig(
            account="bot@example.com",
            allowed_senders=["alice@example.com"],
            attachment_dir=tmpdir,
        )
        ch = EmailChannel(in_queue, config)

        # Gmail API returns filename with \u202f (narrow no-break space)
        unicode_fname = "Screenshot 2026-03-18 at 5.13.10\u202fAM.png"
        normal_fname = "Screenshot 2026-03-18 at 5.13.10 AM.png"
        thread = {
            "id": "thread_1",
            "messages": [
                {
                    "id": "msg_1",
                    "from": "alice@example.com",
                    "subject": "Screenshot",
                    "body": "See attached.",
                    "attachments": [
                        {"filename": unicode_fname, "mimeType": "image/png", "size": 4096},
                    ],
                }
            ],
        }

        # gog also saves the file with \u202f in the filename
        prefixed_unicode = f"19d011139876056b_ANGjdJ83_{unicode_fname}"
        def fake_download(thread_id, out_dir, account):
            os.makedirs(out_dir, exist_ok=True)
            filepath = os.path.join(out_dir, prefixed_unicode)
            with open(filepath, "wb") as f:
                f.write(b"\x89PNG fake image data")

        with patch("src.channels.email.gog") as mock_gog:
            mock_gog.search.return_value = [{"id": "thread_1"}]
            mock_gog.thread_get.return_value = thread
            mock_gog.thread_download_attachments.side_effect = fake_download
            await ch._poll_once()

        msg = in_queue.get_nowait()
        assert msg.attachments is not None

        att = msg.attachments[0]
        # Filename should be normalized to regular spaces
        assert att["filename"] == normal_fname
        assert "\u202f" not in att["path"]

        # The file must be openable at the manifest path (with regular spaces)
        with open(att["path"], "rb") as f:
            data = f.read()
        assert data == b"\x89PNG fake image data"


@pytest.mark.asyncio
async def test_attachment_missing_from_disk_is_excluded(in_queue):
    """If gog doesn't download the file, it should NOT appear in the manifest."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = EmailChannelConfig(
            account="bot@example.com",
            allowed_senders=["alice@example.com"],
            attachment_dir=tmpdir,
        )
        ch = EmailChannel(in_queue, config)

        thread = {
            "id": "thread_1",
            "messages": [
                {
                    "id": "msg_1",
                    "from": "alice@example.com",
                    "subject": "Fwd: Screenshot",
                    "body": "See attached.",
                    "attachments": [
                        {"filename": "screenshot.png", "mimeType": "image/png", "size": 4096},
                    ],
                }
            ],
        }

        # gog "succeeds" but writes NO files (e.g. forwarded email attachment not downloadable)
        def fake_download_noop(thread_id, out_dir, account):
            pass  # directory created by our code, but gog downloads nothing

        with patch("src.channels.email.gog") as mock_gog:
            mock_gog.search.return_value = [{"id": "thread_1"}]
            mock_gog.thread_get.return_value = thread
            mock_gog.thread_download_attachments.side_effect = fake_download_noop
            await ch._poll_once()

        msg = in_queue.get_nowait()
        # Attachment should NOT be in the manifest since file doesn't exist
        assert msg.attachments is None
        assert "screenshot.png" not in msg.content


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
        attachments=None,
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
