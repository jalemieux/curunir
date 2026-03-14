import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from src.channels.gog import (
    check_installed, GogError, labels_list, labels_create,
    search, thread_get, thread_download_attachments, send_reply, thread_modify,
)


def test_check_installed_success():
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        check_installed()  # should not raise


def test_check_installed_not_found():
    with patch("src.channels.gog.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(GogError, match="gog CLI is not installed"):
            check_installed()


def test_labels_list_returns_parsed_json():
    labels_json = json.dumps([{"id": "Label_1", "name": "agent/processed"}])
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=labels_json)
        result = labels_list("bot@example.com")
    assert result == [{"id": "Label_1", "name": "agent/processed"}]
    mock_run.assert_called_once_with(
        ["gog", "gmail", "labels", "list", "--json", "--account", "bot@example.com"],
        capture_output=True, text=True, check=False,
    )


def test_labels_list_command_fails():
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="auth error")
        with pytest.raises(GogError, match="auth error"):
            labels_list("bot@example.com")


def test_labels_create():
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        labels_create("agent/processed", "bot@example.com")
    mock_run.assert_called_once_with(
        ["gog", "gmail", "labels", "create", "agent/processed", "--account", "bot@example.com"],
        capture_output=True, text=True, check=False,
    )


def test_search_returns_threads():
    threads_json = json.dumps([{"id": "thread_1", "snippet": "hello"}])
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=threads_json)
        result = search("-label:agent/processed", "bot@example.com", max_results=20)
    assert result == [{"id": "thread_1", "snippet": "hello"}]
    mock_run.assert_called_once_with(
        ["gog", "gmail", "search", "-label:agent/processed", "--json", "--max", "20", "--account", "bot@example.com"],
        capture_output=True, text=True, check=False,
    )


def test_search_empty_results():
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")
        result = search("-label:agent/processed", "bot@example.com")
    assert result == []


def test_thread_get():
    thread_json = json.dumps({"id": "thread_1", "messages": [{"id": "msg_1"}]})
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=thread_json)
        result = thread_get("thread_1", "bot@example.com")
    assert result == {"id": "thread_1", "messages": [{"id": "msg_1"}]}
    mock_run.assert_called_once_with(
        ["gog", "gmail", "thread", "get", "thread_1", "--json", "--account", "bot@example.com"],
        capture_output=True, text=True, check=False,
    )


def test_thread_download_attachments():
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        thread_download_attachments("thread_1", "/tmp/attachments/thread_1", "bot@example.com")
    mock_run.assert_called_once_with(
        ["gog", "gmail", "thread", "get", "thread_1", "--download", "--out-dir", "/tmp/attachments/thread_1", "--account", "bot@example.com"],
        capture_output=True, text=True, check=False,
    )


def test_send_reply():
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        send_reply(
            to="alice@example.com",
            subject="Re: Hello",
            body="Got it!",
            reply_to_message_id="msg_1",
            account="bot@example.com",
        )
    mock_run.assert_called_once_with(
        [
            "gog", "gmail", "send",
            "--reply-to-message-id", "msg_1",
            "--to", "alice@example.com",
            "--subject", "Re: Hello",
            "--body", "Got it!",
            "--account", "bot@example.com",
        ],
        capture_output=True, text=True, check=False,
    )


def test_thread_modify_add_label():
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        thread_modify("thread_1", add_label="agent/processed", account="bot@example.com")
    mock_run.assert_called_once_with(
        ["gog", "gmail", "thread", "modify", "thread_1", "--add", "agent/processed", "--account", "bot@example.com"],
        capture_output=True, text=True, check=False,
    )


def test_run_json_malformed():
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")
        with pytest.raises(GogError, match="Failed to parse gog JSON output"):
            labels_list("bot@example.com")
