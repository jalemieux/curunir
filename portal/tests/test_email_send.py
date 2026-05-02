import logging

import httpx
import pytest

from portal import email_send
from portal.config import settings


@pytest.mark.asyncio
async def test_send_logs_when_no_api_key(monkeypatch, caplog):
    monkeypatch.setattr(settings, "email_api_key", "")
    with caplog.at_level(logging.WARNING):
        await email_send.send_signin_email("a@example.com", "http://x/y")
    assert any("a@example.com" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_send_calls_postmark_when_api_key_set(monkeypatch):
    monkeypatch.setattr(settings, "email_api_key", "test-key")
    monkeypatch.setattr(settings, "email_from", "from@example.com")

    captured = {}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return httpx.Response(
                200,
                json={"MessageID": "abc"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(email_send.httpx, "AsyncClient", FakeClient)
    await email_send.send_signin_email("b@example.com", "http://x/y")

    assert captured["url"] == email_send.POSTMARK_URL
    assert captured["headers"]["X-Postmark-Server-Token"] == "test-key"
    assert captured["json"]["To"] == "b@example.com"
    assert captured["json"]["From"] == "from@example.com"
    assert "http://x/y" in captured["json"]["HtmlBody"]
    assert "http://x/y" in captured["json"]["TextBody"]
