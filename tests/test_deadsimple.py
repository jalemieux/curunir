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
    """Real deadsimple v0.1.0 envelope: {data: {messages, count}, meta}."""
    respx.get("https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages").mock(
        return_value=httpx.Response(200, json={
            "data": {
                "messages": [
                    {"message_id": "m1", "thread_id": "t1", "direction": "inbound",
                     "from_email": "a@x.com", "subject": "hi", "created_at": "2026-05-14T15:30:00Z"},
                    {"message_id": "m2", "thread_id": "t2", "direction": "outbound",
                     "from_email": "bot@x.com", "subject": "re: hi", "created_at": "2026-05-14T15:29:00Z"},
                ],
                "count": 2,
            },
            "meta": {"timestamp": "2026-05-14T15:30:00Z", "request_id": "rq-1"},
        })
    )
    page = await client.list_messages(limit=50)
    assert len(page["messages"]) == 2
    assert page["next_cursor"] is None


@pytest.mark.asyncio
@respx.mock
async def test_list_messages_passes_cursor(client):
    route = respx.get("https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages").mock(
        return_value=httpx.Response(200, json={"data": {"messages": [], "count": 0}})
    )
    await client.list_messages(limit=20, cursor="cur-abc")
    sent = route.calls.last.request
    assert sent.url.params["limit"] == "20"
    assert sent.url.params["cursor"] == "cur-abc"


@pytest.mark.asyncio
@respx.mock
async def test_list_messages_legacy_list_envelope(client):
    """Defensive: client also accepts a top-level data-as-list shape."""
    respx.get("https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages").mock(
        return_value=httpx.Response(200, json={
            "data": [{"message_id": "m1", "created_at": "2026-05-14T15:30:00Z"}],
            "next_cursor": "cur-x",
        })
    )
    page = await client.list_messages(limit=50)
    assert len(page["messages"]) == 1
    assert page["next_cursor"] == "cur-x"


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
async def test_send_reply_includes_html_body_when_provided(client):
    route = respx.post(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages/m1/reply"
    ).mock(return_value=httpx.Response(201, json={"data": {"message_id": "m2"}}))
    await client.send_reply(
        in_reply_to="m1", to="alice@example.com",
        text_body="Hi back", html_body="<p>Hi back</p>",
    )
    import json as _json
    parsed = _json.loads(route.calls.last.request.content.decode())
    assert parsed["text_body"] == "Hi back"
    assert parsed["html_body"] == "<p>Hi back</p>"


@pytest.mark.asyncio
@respx.mock
async def test_send_reply_omits_html_body_when_absent(client):
    route = respx.post(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages/m1/reply"
    ).mock(return_value=httpx.Response(201, json={"data": {"message_id": "m2"}}))
    await client.send_reply(in_reply_to="m1", to="alice@example.com", text_body="Hi back")
    import json as _json
    parsed = _json.loads(route.calls.last.request.content.decode())
    assert "html_body" not in parsed


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


@pytest.mark.parametrize("bad_to", [
    "alice@example.com.evil.tld",        # suffix attack
    "evilalice@example.com.attacker.tld",  # prefix + suffix
    '"alice@example.com" <attacker@evil.tld>',  # display-name spoof
    "alice@example.com, evil@evil.com",  # mixed — must block on disallowed
])
@pytest.mark.asyncio
@respx.mock
async def test_send_reply_rejects_allowlist_bypass_attempts(restricted_client, bad_to):
    route = respx.post(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages/m1/reply"
    ).mock(return_value=httpx.Response(201, json={}))
    with pytest.raises(DeadsimpleError):
        await restricted_client.send_reply(
            in_reply_to="m1", to=bad_to, text_body="hi"
        )
    assert not route.called


@pytest.mark.parametrize("good_to", [
    "alice@example.com",
    "Alice@Example.COM",
    '"Alice" <alice@example.com>',
])
@pytest.mark.asyncio
@respx.mock
async def test_send_reply_accepts_allowed_recipient_variants(restricted_client, good_to):
    route = respx.post(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages/m1/reply"
    ).mock(return_value=httpx.Response(201, json={"data": {"message_id": "m2"}}))
    await restricted_client.send_reply(
        in_reply_to="m1", to=good_to, text_body="hi"
    )
    assert route.called


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
async def test_send_with_attachments_includes_html_body_when_provided(client, tmp_path):
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
        html_body="<p>see attached</p>",
    )
    import json as _json
    parsed = _json.loads(route.calls.last.request.content.decode())
    assert parsed["html_body"] == "<p>see attached</p>"


@pytest.mark.asyncio
@respx.mock
async def test_send_with_attachments_omits_html_body_when_absent(client, tmp_path):
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
    import json as _json
    parsed = _json.loads(route.calls.last.request.content.decode())
    assert "html_body" not in parsed


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


@pytest.mark.asyncio
@respx.mock
async def test_send_email_posts_with_to_subject_text(client):
    route = respx.post(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages"
    ).mock(return_value=httpx.Response(201, json={"data": {"message_id": "m9"}}))
    await client.send_email(
        to="alice@example.com", subject="Hi", text_body="hello"
    )
    assert route.called
    req = route.calls.last.request
    import json as _json
    parsed = _json.loads(req.content.decode())
    assert parsed["to"] == ["alice@example.com"]
    assert parsed["subject"] == "Hi"
    assert parsed["text_body"] == "hello"
    assert "in_reply_to" not in parsed
    assert "references" not in parsed


@pytest.mark.asyncio
@respx.mock
async def test_send_email_with_cc_bcc_html_attachments(client, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_bytes(b"data")
    route = respx.post(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages"
    ).mock(return_value=httpx.Response(201, json={"data": {"message_id": "m9"}}))
    await client.send_email(
        to=["alice@example.com", "carol@example.com"],
        subject="Hi",
        text_body="hello",
        html_body="<p>hello</p>",
        cc=["bob@example.com"],
        bcc=["dan@example.com"],
        attachment_paths=[str(f)],
    )
    req = route.calls.last.request
    import json as _json
    parsed = _json.loads(req.content.decode())
    assert parsed["to"] == ["alice@example.com", "carol@example.com"]
    assert parsed["cc"] == ["bob@example.com"]
    assert parsed["bcc"] == ["dan@example.com"]
    assert parsed["html_body"] == "<p>hello</p>"
    assert len(parsed["attachments"]) == 1
    assert parsed["attachments"][0]["filename"] == "doc.txt"


@pytest.mark.asyncio
@respx.mock
async def test_send_email_blocked_by_allowlist(restricted_client):
    route = respx.post(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages"
    ).mock(return_value=httpx.Response(201, json={}))
    with pytest.raises(DeadsimpleError):
        await restricted_client.send_email(
            to="evil@evil.com", subject="x", text_body="x"
        )
    assert not route.called


def test_build_client_from_env_raises_on_missing_creds(monkeypatch):
    from src.channels.deadsimple import build_client_from_env, DeadsimpleError
    monkeypatch.delenv("DEADSIMPLE_API_KEY", raising=False)
    monkeypatch.delenv("DEADSIMPLE_INBOX_ID", raising=False)
    with pytest.raises(DeadsimpleError):
        build_client_from_env()


def test_build_client_from_env_constructs_client(monkeypatch):
    from src.channels.deadsimple import build_client_from_env
    monkeypatch.setenv("DEADSIMPLE_API_KEY", "dse_xyz")
    monkeypatch.setenv("DEADSIMPLE_INBOX_ID", "inbox-1")
    monkeypatch.setenv("EMAIL_ALLOWED_SENDERS", "a@x.com,b@x.com")
    monkeypatch.setenv("EMAIL_RESTRICT_OUTBOUND", "false")
    c = build_client_from_env()
    assert c.api_key == "dse_xyz"
    assert c.inbox_id == "inbox-1"
    assert c.allowed_recipients == ["a@x.com", "b@x.com"]
    assert c.restrict_outbound is False
