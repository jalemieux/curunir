import hashlib
import hmac
import json
import time

import pytest

from portal import billing, db
from portal.config import settings


WEBHOOK_SECRET = "whsec_test"

PRICE = {
    "id": "price_basic",
    "active": True,
    "unit_amount": 2000,
    "currency": "usd",
    "recurring": {"interval": "month", "interval_count": 1},
    "product": {
        "active": True,
        "name": "Basic <plan>",
        "description": "The starter plan",
        "marketing_features": [{"name": "Memory"}, {"name": "Fact-checking"}],
    },
}


@pytest.fixture
def stripe_on(monkeypatch):
    """Configure billing and replace the Stripe HTTP call with a recorder."""
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_webhook_secret", WEBHOOK_SECRET)
    monkeypatch.setattr(settings, "stripe_price_ids", "price_basic")
    monkeypatch.setattr(billing, "_plan_cache", None)
    monkeypatch.setattr(billing, "_rate_limited", lambda ip: False)
    calls = []

    async def fake_stripe(method, path, *, params=None, data=None):
        calls.append((method, path, data))
        if path.startswith("/prices/"):
            return PRICE
        if path == "/checkout/sessions":
            return {"url": "https://checkout.stripe.com/c/pay/cs_test_123"}
        raise AssertionError(f"unexpected Stripe call {method} {path}")

    monkeypatch.setattr(billing, "_stripe", fake_stripe)
    return calls


def _signed(event: dict, secret: str = WEBHOOK_SECRET, ts: int | None = None):
    payload = json.dumps(event).encode()
    ts = int(time.time()) if ts is None else ts
    sig = hmac.new(
        secret.encode(), str(ts).encode() + b"." + payload, hashlib.sha256
    ).hexdigest()
    return payload, {"stripe-signature": f"t={ts},v1={sig}"}


@pytest.mark.asyncio
async def test_billing_is_off_until_configured(client):
    # No Stripe env in the test settings: every billing route 404s.
    assert (await client.get("/pricing")).status_code == 404
    assert (await client.get("/billing/success")).status_code == 404
    resp = await client.post("/billing/checkout", data={"price_id": "price_basic"})
    assert resp.status_code == 404
    assert (await client.post("/billing/webhook", content=b"{}")).status_code == 404


@pytest.mark.asyncio
async def test_pricing_lists_plans_from_stripe(client, stripe_on):
    resp = await client.get("/pricing")
    assert resp.status_code == 200
    body = resp.text
    # Product text comes from Stripe, so it must be escaped.
    assert "Basic &lt;plan&gt;" in body
    assert "$20" in body
    assert "per month" in body
    assert "Fact-checking" in body
    assert 'name="price_id" value="price_basic"' in body


@pytest.mark.asyncio
async def test_pricing_skips_inactive_or_one_time_prices(client, stripe_on, monkeypatch):
    async def one_time(method, path, *, params=None, data=None):
        return {**PRICE, "recurring": None}

    monkeypatch.setattr(billing, "_stripe", one_time)
    resp = await client.get("/pricing")
    assert resp.status_code == 200
    assert "No plans are available" in resp.text


@pytest.mark.asyncio
async def test_checkout_redirects_to_stripe(client, stripe_on):
    resp = await client.post(
        "/billing/checkout", data={"price_id": "price_basic"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "https://checkout.stripe.com/c/pay/cs_test_123"
    method, path, data = stripe_on[-1]
    assert (method, path) == ("POST", "/checkout/sessions")
    assert data["mode"] == "subscription"
    assert data["line_items[0][price]"] == "price_basic"
    assert data["success_url"] == "http://localhost:8000/billing/success"
    assert data["cancel_url"] == "http://localhost:8000/pricing"


@pytest.mark.asyncio
async def test_checkout_rejects_a_price_not_on_offer(client, stripe_on):
    resp = await client.post(
        "/billing/checkout", data={"price_id": "price_other"}, follow_redirects=False
    )
    assert resp.status_code == 400
    assert not any(path == "/checkout/sessions" for _, path, _ in stripe_on)


@pytest.mark.asyncio
async def test_webhook_rejects_bad_or_stale_signature(client, stripe_on):
    event = {"type": "customer.subscription.updated", "data": {"object": {"id": "sub_1"}}}

    payload, headers = _signed(event, secret="whsec_wrong")
    resp = await client.post("/billing/webhook", content=payload, headers=headers)
    assert resp.status_code == 400

    payload, headers = _signed(event, ts=int(time.time()) - 3600)
    resp = await client.post("/billing/webhook", content=payload, headers=headers)
    assert resp.status_code == 400

    resp = await client.post("/billing/webhook", content=payload)
    assert resp.status_code == 400
    assert await db.get_subscription("sub_1") is None


@pytest.mark.asyncio
async def test_webhook_records_subscription_in_either_event_order(client, stripe_on):
    sub_event = {
        "type": "customer.subscription.created",
        "data": {"object": {
            "id": "sub_1",
            "customer": "cus_1",
            "status": "active",
            "items": {"data": [{
                "price": {"id": "price_basic"},
                "current_period_end": 1900000000,
            }]},
        }},
    }
    checkout_event = {
        "type": "checkout.session.completed",
        "data": {"object": {
            "mode": "subscription",
            "subscription": "sub_1",
            "customer": "cus_1",
            "customer_details": {"email": "Buyer@Example.com"},
        }},
    }
    for event in (sub_event, checkout_event):
        payload, headers = _signed(event)
        resp = await client.post("/billing/webhook", content=payload, headers=headers)
        assert resp.status_code == 200

    row = await db.get_subscription("sub_1")
    assert row["email"] == "buyer@example.com"
    assert row["stripe_customer_id"] == "cus_1"
    assert row["status"] == "active"
    assert row["price_id"] == "price_basic"
    assert int(row["current_period_end"].timestamp()) == 1900000000

    # A later cancellation updates the same row and keeps the email.
    sub_event["type"] = "customer.subscription.deleted"
    sub_event["data"]["object"]["status"] = "canceled"
    payload, headers = _signed(sub_event)
    resp = await client.post("/billing/webhook", content=payload, headers=headers)
    assert resp.status_code == 200
    row = await db.get_subscription("sub_1")
    assert row["status"] == "canceled"
    assert row["email"] == "buyer@example.com"


@pytest.mark.asyncio
async def test_webhook_ignores_other_event_types(client, stripe_on):
    payload, headers = _signed({"type": "invoice.paid", "data": {"object": {"id": "in_1"}}})
    resp = await client.post("/billing/webhook", content=payload, headers=headers)
    assert resp.status_code == 200


def test_format_amount():
    assert billing._format_amount(2000, "usd") == "$20"
    assert billing._format_amount(1999, "usd") == "$19.99"
    assert billing._format_amount(500, "jpy") == "500 JPY"
    assert billing._format_amount(1500, "cad") == "15 CAD"
