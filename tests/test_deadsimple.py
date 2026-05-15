import time
from unittest.mock import patch

import httpx
import pytest
import respx

from src.channels.deadsimple import DeadsimpleClient, DeadsimpleError


@pytest.fixture
def client():
    return DeadsimpleClient(
        api_key="dse_test_key",
        api_base="https://api.deadsimple.email",
        inbox_id="inbox-uuid-1",
        allowed_recipients=[],
        restrict_outbound=False,
    )


@pytest.mark.asyncio
@respx.mock
async def test_request_sets_auth_header(client):
    route = respx.get("https://api.deadsimple.email/v1/inboxes/inbox-uuid-1").mock(
        return_value=httpx.Response(200, json={"data": {"inbox_id": "inbox-uuid-1"}})
    )
    await client.validate_inbox()
    assert route.called
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer dse_test_key"


@pytest.mark.asyncio
@respx.mock
async def test_validate_inbox_raises_on_404(client):
    respx.get("https://api.deadsimple.email/v1/inboxes/inbox-uuid-1").mock(
        return_value=httpx.Response(404, json={"error": {"code": "not_found", "message": "no"}})
    )
    with pytest.raises(DeadsimpleError) as exc:
        await client.validate_inbox()
    assert "404" in str(exc.value) or "not_found" in str(exc.value)


@pytest.mark.asyncio
@respx.mock
async def test_request_retries_once_on_429(client):
    reset_at = int(time.time()) + 1
    route = respx.get("https://api.deadsimple.email/v1/inboxes/inbox-uuid-1").mock(
        side_effect=[
            httpx.Response(429, headers={"X-RateLimit-Reset": str(reset_at)}, json={"error": {"code": "rate_limited"}}),
            httpx.Response(200, json={"data": {"inbox_id": "inbox-uuid-1"}}),
        ]
    )
    with patch("src.channels.deadsimple.asyncio.sleep", new_callable=_AsyncNoop):
        await client.validate_inbox()
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_request_raises_after_second_429(client):
    route = respx.get("https://api.deadsimple.email/v1/inboxes/inbox-uuid-1").mock(
        return_value=httpx.Response(429, headers={"X-RateLimit-Reset": "0"}, json={"error": {"code": "rate_limited"}})
    )
    with patch("src.channels.deadsimple.asyncio.sleep", new_callable=_AsyncNoop):
        with pytest.raises(DeadsimpleError):
            await client.validate_inbox()
    assert route.call_count == 2


class _AsyncNoop:
    """patch helper: replaces asyncio.sleep with an awaitable that returns immediately."""
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, delay: float):
        self.calls.append(delay)


@pytest.mark.asyncio
@respx.mock
async def test_list_messages_single_page(client):
    respx.get("https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages").mock(
        return_value=httpx.Response(200, json={
            "data": [
                {"message_id": "m1", "thread_id": "t1", "direction": "inbound",
                 "from_email": "a@x.com", "subject": "hi", "created_at": "2026-05-14T15:30:00Z"},
                {"message_id": "m2", "thread_id": "t2", "direction": "outbound",
                 "from_email": "bot@x.com", "subject": "re: hi", "created_at": "2026-05-14T15:29:00Z"},
            ],
            "next_cursor": None,
        })
    )
    page = await client.list_messages(limit=50)
    assert len(page["data"]) == 2
    assert page["next_cursor"] is None


@pytest.mark.asyncio
@respx.mock
async def test_list_messages_passes_cursor(client):
    route = respx.get("https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages").mock(
        return_value=httpx.Response(200, json={"data": [], "next_cursor": None})
    )
    await client.list_messages(limit=20, cursor="cur-abc")
    sent = route.calls.last.request
    assert sent.url.params["limit"] == "20"
    assert sent.url.params["cursor"] == "cur-abc"


@pytest.mark.asyncio
@respx.mock
async def test_get_message_returns_detail(client):
    respx.get(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages/m1"
    ).mock(return_value=httpx.Response(200, json={
        "data": {
            "message_id": "m1", "thread_id": "t1", "direction": "inbound",
            "from_email": "a@x.com", "subject": "hi",
            "text_body": "Hello there", "html_body": "<p>Hello</p>",
            "attachments": [],
            "created_at": "2026-05-14T15:30:00Z",
        }
    }))
    msg = await client.get_message("m1")
    assert msg["message_id"] == "m1"
    assert msg["text_body"] == "Hello there"


@pytest.mark.asyncio
@respx.mock
async def test_download_attachment_writes_bytes(client, tmp_path):
    respx.get(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages/m1/attachments/a1"
    ).mock(return_value=httpx.Response(200, json={
        "data": {"download_url": "https://signed.example.com/file.pdf?token=xyz"}
    }))
    respx.get("https://signed.example.com/file.pdf").mock(
        return_value=httpx.Response(200, content=b"PDF-BYTES-HERE")
    )

    dest = tmp_path / "report.pdf"
    await client.download_attachment("m1", "a1", dest)

    assert dest.exists()
    assert dest.read_bytes() == b"PDF-BYTES-HERE"


@pytest.mark.asyncio
@respx.mock
async def test_download_attachment_raises_when_url_fetch_fails(client, tmp_path):
    respx.get(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages/m1/attachments/a1"
    ).mock(return_value=httpx.Response(200, json={
        "data": {"download_url": "https://signed.example.com/file.pdf"}
    }))
    respx.get("https://signed.example.com/file.pdf").mock(
        return_value=httpx.Response(403, text="expired")
    )

    with pytest.raises(DeadsimpleError):
        await client.download_attachment("m1", "a1", tmp_path / "out.pdf")


@pytest.fixture
def restricted_client():
    return DeadsimpleClient(
        api_key="dse_test",
        api_base="https://api.deadsimple.email",
        inbox_id="inbox-uuid-1",
        allowed_recipients=["alice@example.com"],
        restrict_outbound=True,
    )


@pytest.mark.asyncio
@respx.mock
async def test_send_reply_posts_to_reply_endpoint(client):
    route = respx.post(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages/m1/reply"
    ).mock(return_value=httpx.Response(201, json={"data": {"message_id": "m2"}}))
    await client.send_reply(
        in_reply_to="m1", to="alice@example.com", text_body="Hi back"
    )
    assert route.called
    req = route.calls.last.request
    assert req.headers["idempotency-key"] == "reply-m1"
    import json as _json
    parsed = _json.loads(req.content.decode())
    assert parsed["text_body"] == "Hi back"


@pytest.mark.asyncio
@respx.mock
async def test_send_reply_blocked_by_allowlist(restricted_client):
    route = respx.post(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages/m1/reply"
    ).mock(return_value=httpx.Response(201, json={}))
    with pytest.raises(DeadsimpleError) as exc:
        await restricted_client.send_reply(
            in_reply_to="m1", to="evil@evil.com", text_body="hi"
        )
    assert "Outbound" in str(exc.value)
    assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_send_with_attachments_uses_messages_endpoint(client, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_bytes(b"hello")

    route = respx.post(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages"
    ).mock(return_value=httpx.Response(201, json={"data": {"message_id": "m9"}}))

    await client.send_with_attachments(
        in_reply_to="m1",
        to="alice@example.com",
        subject="Re: hi",
        text_body="see attached",
        attachment_paths=[str(f)],
    )
    assert route.called
    req = route.calls.last.request
    import json as _json
    parsed = _json.loads(req.content.decode())
    assert parsed["to"] == ["alice@example.com"]
    assert parsed["subject"] == "Re: hi"
    assert parsed["in_reply_to"] == "m1"
    assert parsed["references"] == ["m1"]
    assert len(parsed["attachments"]) == 1
    att = parsed["attachments"][0]
    assert att["filename"] == "doc.txt"
    import base64 as _b64
    assert _b64.b64decode(att["data"]) == b"hello"


@pytest.mark.asyncio
@respx.mock
async def test_send_with_attachments_blocked_by_allowlist(restricted_client, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_bytes(b"x")
    route = respx.post(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages"
    ).mock(return_value=httpx.Response(201, json={}))
    with pytest.raises(DeadsimpleError):
        await restricted_client.send_with_attachments(
            in_reply_to="m1",
            to="evil@evil.com",
            subject="re",
            text_body="x",
            attachment_paths=[str(f)],
        )
    assert not route.called
