import asyncio
import errno
import http.client
import logging
import os
import ssl
import tempfile
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from googleapiclient.errors import HttpError

from src.channels.email import EmailChannel
from src.channels.gmail import GmailError
from src.config import EmailChannelConfig


@pytest.fixture
def email_config():
    return EmailChannelConfig(
        enabled=True,
        service_account_file="/fake/key.json",
        delegated_user="bot@example.com",
        poll_interval_sec=1,
        allowed_senders=["alice@example.com"],
        processed_label="agent/processed",
        attachment_dir="/tmp/attachments",
    )


@pytest.fixture
def in_queue():
    return asyncio.Queue()


def _make_channel(in_queue, config):
    """Create an EmailChannel with build_service mocked out."""
    with patch("src.channels.email.gmail.build_service", return_value=MagicMock()):
        return EmailChannel(in_queue, config)


def test_constructor(email_config, in_queue):
    ch = _make_channel(in_queue, email_config)
    assert ch.in_queue is in_queue
    assert ch.config is email_config
    assert ch.service is not None
    assert ch.poll_interval == 1
    assert ch.allowed_senders == ["alice@example.com"]
    assert ch.processed_label == "agent/processed"
    assert ch.attachment_dir == "/tmp/attachments"
    assert ch.last_seen == {}


@pytest.mark.asyncio
async def test_ensure_label_exists_already(email_config, in_queue):
    ch = _make_channel(in_queue, email_config)
    with patch("src.channels.email.gmail") as mock_gmail:
        mock_gmail.labels_list.return_value = [{"name": "agent/processed"}]
        await ch._ensure_label()
    mock_gmail.labels_list.assert_called_once_with(ch.service)
    mock_gmail.labels_create.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_label_creates_missing(email_config, in_queue):
    ch = _make_channel(in_queue, email_config)
    with patch("src.channels.email.gmail") as mock_gmail:
        mock_gmail.labels_list.return_value = [{"name": "INBOX"}]
        await ch._ensure_label()
    mock_gmail.labels_create.assert_called_once_with("agent/processed", ch.service)


from src.channels.base import IncomingMessage


@pytest.mark.asyncio
async def test_poll_once_pushes_message(email_config, in_queue):
    ch = _make_channel(in_queue, email_config)

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

    with patch("src.channels.email.gmail") as mock_gmail:
        mock_gmail.search.return_value = [{"id": "thread_1"}]
        mock_gmail.thread_get.return_value = thread
        await ch._poll_once()

    msg = in_queue.get_nowait()
    assert msg.content == "[channel: email, from: alice@example.com]\nHi there!"
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
    ch = _make_channel(in_queue, email_config)

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

    with patch("src.channels.email.gmail") as mock_gmail:
        mock_gmail.search.return_value = [{"id": "thread_1"}]
        mock_gmail.thread_get.return_value = thread
        await ch._poll_once()

    assert in_queue.empty()
    assert ch.last_seen["thread_1"] == "msg_1"


@pytest.mark.asyncio
async def test_poll_once_skips_already_seen_messages(email_config, in_queue):
    ch = _make_channel(in_queue, email_config)
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

    with patch("src.channels.email.gmail") as mock_gmail:
        mock_gmail.search.return_value = [{"id": "thread_1"}]
        mock_gmail.thread_get.return_value = thread
        await ch._poll_once()

    msg = in_queue.get_nowait()
    assert msg.content == "[channel: email, from: alice@example.com]\nFollow up!"
    assert msg.reply_address["in_reply_to"] == "msg_2"
    assert in_queue.empty()
    assert ch.last_seen["thread_1"] == "msg_2"


@pytest.mark.asyncio
async def test_poll_once_accepts_all_when_no_allowlist(in_queue):
    config = EmailChannelConfig(
        enabled=True, service_account_file="/fake/key.json",
        delegated_user="bot@example.com", poll_interval_sec=1,
    )
    ch = _make_channel(in_queue, config)

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

    with patch("src.channels.email.gmail") as mock_gmail:
        mock_gmail.search.return_value = [{"id": "thread_1"}]
        mock_gmail.thread_get.return_value = thread
        await ch._poll_once()

    assert not in_queue.empty()


@pytest.mark.asyncio
async def test_poll_once_no_double_re_prefix(email_config, in_queue):
    ch = _make_channel(in_queue, email_config)

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

    with patch("src.channels.email.gmail") as mock_gmail:
        mock_gmail.search.return_value = [{"id": "thread_1"}]
        mock_gmail.thread_get.return_value = thread
        await ch._poll_once()

    msg = in_queue.get_nowait()
    assert msg.reply_address["subject"] == "Re: Hello"


@pytest.mark.asyncio
async def test_poll_once_with_attachments(in_queue):
    with tempfile.TemporaryDirectory() as tmpdir:
        config = EmailChannelConfig(
            service_account_file="/fake/key.json",
            delegated_user="bot@example.com",
            allowed_senders=["alice@example.com"],
            attachment_dir=tmpdir,
        )
        ch = _make_channel(in_queue, config)

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

        def fake_download(thread_id, message, out_dir, service):
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "report.pdf"), "wb") as f:
                f.write(b"fake pdf")

        with patch("src.channels.email.gmail") as mock_gmail:
            mock_gmail.search.return_value = [{"id": "thread_1"}]
            mock_gmail.thread_get.return_value = thread
            mock_gmail.download_attachments.side_effect = fake_download
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
        with open(msg.attachments[0]["path"], "rb") as f:
            assert f.read() == b"fake pdf"


@pytest.mark.asyncio
async def test_attachment_manifest_uses_real_filesystem(in_queue):
    """End-to-end test: download creates files on disk, manifest resolves them,
    and the file is actually openable at the manifest path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = EmailChannelConfig(
            service_account_file="/fake/key.json",
            delegated_user="bot@example.com",
            allowed_senders=["alice@example.com"],
            attachment_dir=tmpdir,
        )
        ch = _make_channel(in_queue, config)

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

        def fake_download(thread_id, message, out_dir, service):
            os.makedirs(out_dir, exist_ok=True)
            filepath = os.path.join(out_dir, "Screenshot 2026-03-18 at 5.13.10 AM.png")
            with open(filepath, "wb") as f:
                f.write(b"\x89PNG fake image data")

        with patch("src.channels.email.gmail") as mock_gmail:
            mock_gmail.search.return_value = [{"id": "thread_1"}]
            mock_gmail.thread_get.return_value = thread
            mock_gmail.download_attachments.side_effect = fake_download
            await ch._poll_once()

        msg = in_queue.get_nowait()
        assert msg.attachments is not None
        assert len(msg.attachments) == 1

        att = msg.attachments[0]
        assert att["filename"] == "Screenshot 2026-03-18 at 5.13.10 AM.png"

        with open(att["path"], "rb") as f:
            data = f.read()
        assert data == b"\x89PNG fake image data"

        assert att["path"] in msg.content


@pytest.mark.asyncio
async def test_attachment_unicode_whitespace_normalized(in_queue):
    """Gmail uses \\u202f (narrow no-break space) in filenames like
    'Screenshot 2026-03-18 at 5.13.10\\u202fAM.png'. LLMs convert this
    to a regular space in tool calls, causing file-not-found. Verify
    we normalize the filename on disk so the path always uses regular spaces."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = EmailChannelConfig(
            service_account_file="/fake/key.json",
            delegated_user="bot@example.com",
            allowed_senders=["alice@example.com"],
            attachment_dir=tmpdir,
        )
        ch = _make_channel(in_queue, config)

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

        def fake_download(thread_id, message, out_dir, service):
            os.makedirs(out_dir, exist_ok=True)
            filepath = os.path.join(out_dir, unicode_fname)
            with open(filepath, "wb") as f:
                f.write(b"\x89PNG fake image data")

        with patch("src.channels.email.gmail") as mock_gmail:
            mock_gmail.search.return_value = [{"id": "thread_1"}]
            mock_gmail.thread_get.return_value = thread
            mock_gmail.download_attachments.side_effect = fake_download
            await ch._poll_once()

        msg = in_queue.get_nowait()
        assert msg.attachments is not None

        att = msg.attachments[0]
        assert att["filename"] == normal_fname
        assert "\u202f" not in att["path"]

        with open(att["path"], "rb") as f:
            data = f.read()
        assert data == b"\x89PNG fake image data"


@pytest.mark.asyncio
async def test_attachment_missing_from_disk_is_excluded(in_queue):
    """If download doesn't write the file, it should NOT appear in the manifest."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = EmailChannelConfig(
            service_account_file="/fake/key.json",
            delegated_user="bot@example.com",
            allowed_senders=["alice@example.com"],
            attachment_dir=tmpdir,
        )
        ch = _make_channel(in_queue, config)

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

        def fake_download_noop(thread_id, message, out_dir, service):
            pass

        with patch("src.channels.email.gmail") as mock_gmail:
            mock_gmail.search.return_value = [{"id": "thread_1"}]
            mock_gmail.thread_get.return_value = thread
            mock_gmail.download_attachments.side_effect = fake_download_noop
            await ch._poll_once()

        msg = in_queue.get_nowait()
        assert msg.attachments is None
        assert "screenshot.png" not in msg.content


@pytest.mark.asyncio
async def test_attachment_unsupported_mime_dropped(in_queue):
    """Email channel mirrors the portal/ws allowlist: unsupported mimes are
    dropped from the manifest with a warning, supported ones flow through."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = EmailChannelConfig(
            service_account_file="/fake/key.json",
            delegated_user="bot@example.com",
            allowed_senders=["alice@example.com"],
            attachment_dir=tmpdir,
        )
        ch = _make_channel(in_queue, config)

        thread = {
            "id": "thread_1",
            "messages": [
                {
                    "id": "msg_1",
                    "from": "alice@example.com",
                    "subject": "Mixed",
                    "body": "See attached.",
                    "attachments": [
                        {"filename": "ok.pdf", "mimeType": "application/pdf", "size": 4096},
                        {"filename": "bad.zip", "mimeType": "application/zip", "size": 4096},
                    ],
                }
            ],
        }

        def fake_download(thread_id, message, out_dir, service):
            os.makedirs(out_dir, exist_ok=True)
            for name in ("ok.pdf", "bad.zip"):
                with open(os.path.join(out_dir, name), "wb") as f:
                    f.write(b"x" * 4096)

        with patch("src.channels.email.gmail") as mock_gmail:
            mock_gmail.search.return_value = [{"id": "thread_1"}]
            mock_gmail.thread_get.return_value = thread
            mock_gmail.download_attachments.side_effect = fake_download
            await ch._poll_once()

        msg = in_queue.get_nowait()
        assert msg.attachments is not None
        assert len(msg.attachments) == 1
        assert msg.attachments[0]["filename"] == "ok.pdf"


@pytest.mark.asyncio
async def test_attachment_oversized_pdf_dropped(in_queue):
    """An oversized PDF (>10 MB) is dropped at email intake."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = EmailChannelConfig(
            service_account_file="/fake/key.json",
            delegated_user="bot@example.com",
            allowed_senders=["alice@example.com"],
            attachment_dir=tmpdir,
        )
        ch = _make_channel(in_queue, config)

        oversized = 11 * 1024 * 1024
        thread = {
            "id": "thread_1",
            "messages": [
                {
                    "id": "msg_1",
                    "from": "alice@example.com",
                    "subject": "Huge",
                    "body": "See attached.",
                    "attachments": [
                        {"filename": "huge.pdf", "mimeType": "application/pdf", "size": oversized},
                    ],
                }
            ],
        }

        def fake_download(thread_id, message, out_dir, service):
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "huge.pdf"), "wb") as f:
                f.write(b"\x00" * oversized)

        with patch("src.channels.email.gmail") as mock_gmail:
            mock_gmail.search.return_value = [{"id": "thread_1"}]
            mock_gmail.thread_get.return_value = thread
            mock_gmail.download_attachments.side_effect = fake_download
            await ch._poll_once()

        msg = in_queue.get_nowait()
        assert msg.attachments is None


@pytest.mark.asyncio
async def test_poll_once_continues_on_thread_error(email_config, in_queue):
    """If one thread fails to fetch, other threads still get processed."""
    ch = _make_channel(in_queue, email_config)

    good_thread = {
        "id": "thread_2",
        "messages": [
            {"id": "msg_2", "from": "alice@example.com", "subject": "OK", "body": "Works!", "attachments": []},
        ],
    }

    with patch("src.channels.email.gmail") as mock_gmail:
        mock_gmail.GmailError = GmailError
        mock_gmail.search.return_value = [{"id": "thread_1"}, {"id": "thread_2"}]
        mock_gmail.thread_get.side_effect = [GmailError("network error"), good_thread]
        await ch._poll_once()

    msg = in_queue.get_nowait()
    assert msg.content == "[channel: email, from: alice@example.com]\nWorks!"


from src.channels.base import OutgoingMessage


@pytest.mark.asyncio
async def test_send_reply_and_label(email_config, in_queue):
    ch = _make_channel(in_queue, email_config)

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

    with patch("src.channels.email.gmail") as mock_gmail:
        await ch.send(msg)

    mock_gmail.send_reply.assert_called_once_with(
        to="alice@example.com",
        subject="Re: Hello",
        body="Got it, thanks!",
        reply_to_message_id="msg_1",
        service=ch.service,
        attachments=None,
    )
    mock_gmail.thread_modify.assert_called_once_with(
        "thread_1", add_label="agent/processed", service=ch.service,
    )


@pytest.mark.asyncio
async def test_send_failure_does_not_label(email_config, in_queue):
    ch = _make_channel(in_queue, email_config)

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

    with patch("src.channels.email.gmail") as mock_gmail:
        mock_gmail.GmailError = GmailError
        mock_gmail.send_reply.side_effect = GmailError("send failed")
        await ch.send(msg)

    mock_gmail.thread_modify.assert_not_called()


@pytest.mark.asyncio
async def test_send_skips_streaming_deltas(email_config, in_queue):
    """Streaming deltas (final=False) must NOT trigger an email — otherwise
    every token of the agent's reply becomes a separate email."""
    ch = _make_channel(in_queue, email_config)

    reply_address = {
        "to": "alice@example.com",
        "subject": "Re: Hello",
        "in_reply_to": "msg_1",
    }
    delta = OutgoingMessage(
        content="word",
        channel="email",
        session_id="thread_1",
        reply_address=reply_address,
        delta=True,
        final=False,
    )
    tool_marker = OutgoingMessage(
        content="",
        channel="email",
        session_id="thread_1",
        reply_address=reply_address,
        tool_calls=["Bash echo"],
        final=False,
    )

    with patch("src.channels.email.gmail") as mock_gmail:
        await ch.send(delta)
        await ch.send(tool_marker)

    mock_gmail.send_reply.assert_not_called()
    mock_gmail.thread_modify.assert_not_called()


def _http_error(status):
    """Build a googleapiclient HttpError with the given HTTP status."""
    resp = MagicMock()
    resp.status = status
    return HttpError(resp, b"{}")


def _chained_enetunreach():
    """OSError(ENETUNREACH) chained off a RemoteDisconnected, as seen on #127."""
    try:
        raise http.client.RemoteDisconnected(
            "Remote end closed connection without response"
        )
    except http.client.RemoteDisconnected as ctx:
        exc = OSError(errno.ENETUNREACH, "Network is unreachable")
        exc.__context__ = ctx
        return exc


def _bare_enetunreach():
    """Plain OSError(ENETUNREACH) with no exception chain."""
    return OSError(errno.ENETUNREACH, "Network is unreachable")


async def _run_poll_loop_once(ch, exc):
    """Drive _poll_loop so _poll_once raises `exc`, then break out via cancel."""
    with patch.object(ch, "_poll_once", new_callable=AsyncMock) as mock_poll, \
         patch("src.channels.email.asyncio.sleep", new_callable=AsyncMock):
        mock_poll.side_effect = [exc, asyncio.CancelledError()]
        with pytest.raises(asyncio.CancelledError):
            await ch._poll_loop()


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [
    BrokenPipeError(errno.EPIPE, "Broken pipe"),
    ssl.SSLError("UNEXPECTED_EOF_WHILE_READING"),
    _http_error(503),
    _bare_enetunreach(),
])
async def test_poll_loop_logs_transient_errors_as_warning(email_config, in_queue, caplog, exc):
    """Transient socket/HTTP errors are logged at WARNING, one line, no traceback."""
    ch = _make_channel(in_queue, email_config)
    with caplog.at_level(logging.WARNING, logger="src.channels.email"):
        await _run_poll_loop_once(ch, exc)

    records = [r for r in caplog.records if r.name == "src.channels.email"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].exc_info is None
    assert "Transient email poll error" in records[0].getMessage()


@pytest.mark.asyncio
@pytest.mark.parametrize("make_exc", [_chained_enetunreach, _bare_enetunreach])
async def test_poll_loop_logs_chained_transient_error_as_warning(
    email_config, in_queue, caplog, make_exc
):
    """A transient error anywhere in the __cause__/__context__ chain → WARNING.

    ENETUNREACH is a plain OSError (not a ConnectionError subclass), so the
    classifier must inspect errno and walk the chain — see #127."""
    ch = _make_channel(in_queue, email_config)
    with caplog.at_level(logging.WARNING, logger="src.channels.email"):
        await _run_poll_loop_once(ch, make_exc())

    records = [r for r in caplog.records if r.name == "src.channels.email"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [
    ValueError("nope"),
    OSError(errno.ENOENT, "No such file"),
])
async def test_poll_loop_logs_unknown_errors_as_error(email_config, in_queue, caplog, exc):
    """Unknown errors — including non-network-errno OSError — keep ERROR + traceback."""
    ch = _make_channel(in_queue, email_config)
    with caplog.at_level(logging.WARNING, logger="src.channels.email"):
        await _run_poll_loop_once(ch, exc)

    records = [r for r in caplog.records if r.name == "src.channels.email"]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None
