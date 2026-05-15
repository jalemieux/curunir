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
