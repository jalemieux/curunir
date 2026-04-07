import base64
from unittest.mock import patch, MagicMock, call

import pytest
from googleapiclient.errors import HttpError

from src.channels.gmail import (
    GmailError, build_service, labels_list, labels_create,
    search, thread_get, send_reply, thread_modify, download_attachments,
    _extract_attachments, _normalize_message, _decode_body,
)


def _http_error(status=400, reason="Bad Request"):
    resp = MagicMock(status=status, reason=reason)
    return HttpError(resp, b"error")


@pytest.fixture
def mock_service():
    return MagicMock()


def test_build_service():
    with patch("src.channels.gmail.service_account.Credentials.from_service_account_file") as mock_creds, \
         patch("src.channels.gmail.build") as mock_build:
        mock_creds.return_value = MagicMock()
        build_service("/path/to/key.json", "user@example.com")
    mock_creds.assert_called_once_with(
        "/path/to/key.json",
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
        subject="user@example.com",
    )
    mock_build.assert_called_once_with("gmail", "v1", credentials=mock_creds.return_value)


def test_labels_list(mock_service):
    mock_service.users().labels().list().execute.return_value = {
        "labels": [{"id": "Label_1", "name": "agent/processed"}]
    }
    result = labels_list(mock_service)
    assert result == [{"id": "Label_1", "name": "agent/processed"}]


def test_labels_list_api_error(mock_service):
    mock_service.users().labels().list().execute.side_effect = _http_error()
    with pytest.raises(GmailError, match="Failed to list labels"):
        labels_list(mock_service)


def test_labels_create(mock_service):
    labels_create("agent/processed", mock_service)
    mock_service.users().labels().create.assert_called_once_with(
        userId="me", body={"name": "agent/processed"}
    )


def test_labels_create_api_error(mock_service):
    mock_service.users().labels().create().execute.side_effect = _http_error()
    with pytest.raises(GmailError, match="Failed to create label"):
        labels_create("agent/processed", mock_service)


def test_search_returns_threads(mock_service):
    mock_service.users().threads().list().execute.return_value = {
        "threads": [{"id": "thread_1", "snippet": "hello"}]
    }
    result = search("in:inbox", mock_service, max_results=10)
    assert result == [{"id": "thread_1", "snippet": "hello"}]


def test_search_empty(mock_service):
    mock_service.users().threads().list().execute.return_value = {}
    result = search("in:inbox", mock_service)
    assert result == []


def test_search_api_error(mock_service):
    mock_service.users().threads().list().execute.side_effect = _http_error()
    with pytest.raises(GmailError, match="Failed to search"):
        search("in:inbox", mock_service)


def test_thread_get(mock_service):
    body_data = base64.urlsafe_b64encode(b"Hello world").decode()
    raw_thread = {
        "id": "thread_1",
        "messages": [{
            "id": "msg_1",
            "threadId": "thread_1",
            "payload": {
                "headers": [
                    {"name": "From", "value": "alice@example.com"},
                    {"name": "To", "value": "bot@example.com"},
                    {"name": "Subject", "value": "Hello"},
                ],
                "body": {"data": body_data},
                "parts": [],
            },
        }],
    }
    mock_service.users().threads().get().execute.return_value = raw_thread
    result = thread_get("thread_1", mock_service)
    assert result["id"] == "thread_1"
    assert len(result["messages"]) == 1
    msg = result["messages"][0]
    assert msg["id"] == "msg_1"
    assert msg["from"] == "alice@example.com"
    assert msg["subject"] == "Hello"
    assert msg["body"] == "Hello world"
    assert msg["attachments"] == []


def test_thread_get_api_error(mock_service):
    mock_service.users().threads().get().execute.side_effect = _http_error()
    with pytest.raises(GmailError, match="Failed to get thread"):
        thread_get("thread_1", mock_service)


def test_send_reply(mock_service):
    mock_service.users().messages().get().execute.return_value = {
        "threadId": "thread_1",
        "payload": {
            "headers": [{"name": "Message-ID", "value": "<orig@example.com>"}],
        },
    }
    send_reply(
        to="alice@example.com",
        subject="Re: Hello",
        body="Got it!",
        reply_to_message_id="msg_1",
        service=mock_service,
    )
    mock_service.users().messages().send.assert_called_once()
    send_args = mock_service.users().messages().send.call_args
    assert send_args[1]["userId"] == "me"
    body = send_args[1]["body"]
    assert body["threadId"] == "thread_1"
    assert "raw" in body


def test_send_reply_api_error(mock_service):
    mock_service.users().messages().get().execute.side_effect = _http_error()
    with pytest.raises(GmailError, match="Failed to send reply"):
        send_reply("to@x.com", "subj", "body", "msg_1", mock_service)


def test_thread_modify(mock_service):
    mock_service.users().labels().list().execute.return_value = {
        "labels": [{"id": "Label_99", "name": "agent/processed"}]
    }
    thread_modify("thread_1", add_label="agent/processed", service=mock_service)
    mock_service.users().threads().modify.assert_called_once_with(
        userId="me", id="thread_1",
        body={"addLabelIds": ["Label_99"]},
    )


def test_thread_modify_label_not_found(mock_service):
    mock_service.users().labels().list().execute.return_value = {
        "labels": [{"id": "Label_1", "name": "INBOX"}]
    }
    with pytest.raises(GmailError, match="Label 'agent/processed' not found"):
        thread_modify("thread_1", add_label="agent/processed", service=mock_service)


def test_thread_modify_api_error(mock_service):
    mock_service.users().labels().list().execute.return_value = {
        "labels": [{"id": "Label_99", "name": "agent/processed"}]
    }
    mock_service.users().threads().modify().execute.side_effect = _http_error()
    with pytest.raises(GmailError, match="Failed to modify thread"):
        thread_modify("thread_1", add_label="agent/processed", service=mock_service)


def test_download_attachments(mock_service, tmp_path):
    att_data = base64.urlsafe_b64encode(b"file contents").decode()
    mock_service.users().messages().attachments().get().execute.return_value = {
        "data": att_data,
    }
    message = {
        "id": "msg_1",
        "payload": {
            "parts": [
                {"filename": "report.pdf", "mimeType": "application/pdf",
                 "body": {"attachmentId": "att_1", "size": 1024}},
            ],
        },
    }
    download_attachments("thread_1", message, str(tmp_path), mock_service)

    filepath = tmp_path / "report.pdf"
    assert filepath.exists()
    assert filepath.read_bytes() == b"file contents"


def test_download_attachments_nested(mock_service, tmp_path):
    att_data = base64.urlsafe_b64encode(b"nested data").decode()
    mock_service.users().messages().attachments().get().execute.return_value = {
        "data": att_data,
    }
    message = {
        "id": "msg_1",
        "payload": {
            "parts": [
                {"mimeType": "text/plain", "filename": "", "body": {"data": ""}},
                {
                    "mimeType": "multipart/mixed",
                    "filename": "",
                    "parts": [
                        {"filename": "screenshot.png", "mimeType": "image/png",
                         "body": {"attachmentId": "att_2", "size": 4096}},
                    ],
                },
            ],
        },
    }
    download_attachments("thread_1", message, str(tmp_path), mock_service)
    assert (tmp_path / "screenshot.png").exists()
    assert (tmp_path / "screenshot.png").read_bytes() == b"nested data"


def test_extract_attachments_nested_parts():
    payload = {
        "parts": [
            {"mimeType": "text/plain", "body": {"data": ""}, "filename": ""},
            {
                "mimeType": "multipart/mixed",
                "filename": "",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": ""}, "filename": ""},
                    {"mimeType": "image/png", "filename": "screenshot.png", "body": {"size": 4096}},
                ],
            },
        ]
    }
    attachments = _extract_attachments(payload)
    assert len(attachments) == 1
    assert attachments[0]["filename"] == "screenshot.png"
    assert attachments[0]["mimeType"] == "image/png"
    assert attachments[0]["size"] == 4096


def test_normalize_message():
    body_data = base64.urlsafe_b64encode(b"Test body").decode()
    raw = {
        "id": "msg_1",
        "threadId": "thread_1",
        "payload": {
            "headers": [
                {"name": "From", "value": "alice@example.com"},
                {"name": "To", "value": "bot@example.com"},
                {"name": "Subject", "value": "Test"},
            ],
            "body": {"data": body_data},
        },
    }
    msg = _normalize_message(raw)
    assert msg["id"] == "msg_1"
    assert msg["thread_id"] == "thread_1"
    assert msg["from"] == "alice@example.com"
    assert msg["to"] == "bot@example.com"
    assert msg["subject"] == "Test"
    assert msg["body"] == "Test body"
    assert msg["attachments"] == []


def test_decode_body_multipart():
    plain_data = base64.urlsafe_b64encode(b"Plain text").decode()
    html_data = base64.urlsafe_b64encode(b"<p>HTML</p>").decode()
    payload = {
        "parts": [
            {"mimeType": "text/plain", "body": {"data": plain_data}},
            {"mimeType": "text/html", "body": {"data": html_data}},
        ],
    }
    assert _decode_body(payload) == "Plain text"


def test_decode_body_html_fallback():
    html_data = base64.urlsafe_b64encode(b"<p>HTML</p>").decode()
    payload = {
        "parts": [
            {"mimeType": "text/html", "body": {"data": html_data}},
        ],
    }
    assert _decode_body(payload) == "<p>HTML</p>"


def test_decode_body_empty():
    payload = {"body": {"data": ""}}
    assert _decode_body(payload) == ""
