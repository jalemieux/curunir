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
