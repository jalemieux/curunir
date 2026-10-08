"""Subscription billing through Stripe Checkout.

A public /pricing page lists the plans, each plan's button starts a
Stripe-hosted Checkout session, and a signed webhook records the resulting
subscription in the `subscriptions` table. Card data never touches the portal.

Plans are not defined here: STRIPE_PRICE_IDS names the Stripe Prices to offer,
and the page reads each plan's name, description, features and amount from
Stripe, so a price change is made in the Stripe dashboard only. A plan may
pair its recurring price with a one-time price (`recurring+upfront`) that
Stripe charges with the first payment, e.g. hardware bought outright.

The whole surface 404s until STRIPE_SECRET_KEY and STRIPE_PRICE_IDS are set.
Stripe is called over its REST API with httpx (already a dependency) rather
than the stripe SDK.

A recorded subscription does not create a portal account or invite; that
stays a manual admin step.
"""

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from portal import db
from portal.beta import _client_ip, _rate_limited
from portal.config import settings


logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_STRIPE_API = "https://api.stripe.com/v1"
_PLAN_CACHE_SEC = 300
# Reject webhook deliveries whose signed timestamp is older than this, so a
# captured request can't be replayed later.
_WEBHOOK_TOLERANCE_SEC = 300

# The Stripe account may be shared with other products, whose events reach
# this webhook too. Checkout stamps this on the session and its subscription,
# and the webhook records only objects that carry it.
_APP_TAG = "curunir"

_CURRENCY_SYMBOLS = {"usd": "$", "eur": "€", "gbp": "£"}
# Stripe amounts are in the currency's smallest unit; these have no minor unit.
_ZERO_DECIMAL = {"jpy", "krw", "vnd", "clp", "pyg", "xaf", "xof", "ugx", "rwf"}

_plan_cache: tuple[float, str, list["Plan"]] | None = None


class StripeError(Exception):
    pass


@dataclass
class Plan:
    price_id: str
    name: str
    description: str
    price_display: str
    interval_display: str
    features: list[str]
    upfront_display: str = ""  # one-time amount due with the first payment


def _configured() -> bool:
    return bool(settings.stripe_secret_key and settings.stripe_plans)


def _require_configured() -> None:
    if not _configured():
        raise HTTPException(status.HTTP_404_NOT_FOUND)


async def _stripe(
    method: str, path: str, *, params: Any = None, data: Any = None
) -> dict:
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.request(
            method,
            f"{_STRIPE_API}{path}",
            params=params,
            data=data,
            headers={"Authorization": f"Bearer {settings.stripe_secret_key}"},
        )
    if resp.status_code >= 400:
        try:
            message = resp.json()["error"]["message"]
        except Exception:
            message = resp.text[:200]
        raise StripeError(f"{method} {path} -> {resp.status_code}: {message}")
    return resp.json()


def _format_amount(unit_amount: int, currency: str) -> str:
    currency = currency.lower()
    if currency in _ZERO_DECIMAL:
        number = f"{unit_amount:,}"
    else:
        number = f"{unit_amount / 100:,.2f}".removesuffix(".00")
    symbol = _CURRENCY_SYMBOLS.get(currency)
    return f"{symbol}{number}" if symbol else f"{number} {currency.upper()}"


def _format_interval(recurring: dict) -> str:
    interval = recurring.get("interval", "month")
    count = recurring.get("interval_count", 1)
    return f"per {interval}" if count == 1 else f"every {count} {interval}s"


def _plan_from_price(price: dict) -> Optional[Plan]:
    product = price.get("product")
    recurring = price.get("recurring")
    # Only active, recurring prices on an active product are sold here.
    if not price.get("active") or not recurring or not isinstance(product, dict):
        return None
    if not product.get("active") or price.get("unit_amount") is None:
        return None
    return Plan(
        price_id=price["id"],
        name=product.get("name") or "",
        description=product.get("description") or "",
        price_display=_format_amount(price["unit_amount"], price["currency"]),
        interval_display=_format_interval(recurring),
        features=[
            f["name"] for f in product.get("marketing_features") or [] if f.get("name")
        ],
    )


async def _upfront_display(price_id: str) -> Optional[str]:
    price = await _stripe("GET", f"/prices/{price_id}")
    # An upfront fee must be an active one-time price.
    if not price.get("active") or price.get("recurring") or price.get("unit_amount") is None:
        return None
    return _format_amount(price["unit_amount"], price["currency"])


async def load_plans() -> list[Plan]:
    """Plans in STRIPE_PRICE_IDS order, cached briefly to spare Stripe."""
    global _plan_cache
    spec = settings.stripe_price_ids
    now = time.monotonic()
    if _plan_cache and _plan_cache[1] == spec and now - _plan_cache[0] < _PLAN_CACHE_SEC:
        return _plan_cache[2]
    plans = []
    for price_id, upfront_id in settings.stripe_plans.items():
        price = await _stripe(
            "GET", f"/prices/{price_id}", params={"expand[]": "product"}
        )
        plan = _plan_from_price(price)
        if plan is None:
            logger.warning("billing: price %s is not a sellable plan; skipped", price_id)
            continue
        if upfront_id:
            upfront = await _upfront_display(upfront_id)
            # Never sell the plan without its upfront fee.
            if upfront is None:
                logger.warning(
                    "billing: upfront price %s of plan %s is not an active "
                    "one-time price; plan skipped", upfront_id, price_id,
                )
                continue
            plan.upfront_display = upfront
        plans.append(plan)
    _plan_cache = (now, spec, plans)
    return plans


@router.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request):
    _require_configured()
    try:
        plans = await load_plans()
    except (StripeError, httpx.HTTPError) as e:
        logger.error("billing: could not load plans: %s", e)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE)
    return templates.TemplateResponse(request, "pricing.html", {"plans": plans})


@router.post("/billing/checkout")
async def checkout(request: Request, price_id: str = Form(..., max_length=128)):
    _require_configured()
    if _rate_limited(_client_ip(request)):
        return JSONResponse(
            {"ok": False, "error": "rate_limited"},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    # Only prices the operator listed can be bought, whatever the form says.
    plans = settings.stripe_plans
    if price_id not in plans:
        raise HTTPException(status.HTTP_400_BAD_REQUEST)
    line_items = {
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
    }
    # A one-time price in a subscription-mode session is added to the first
    # invoice only.
    if plans[price_id]:
        line_items["line_items[1][price]"] = plans[price_id]
        line_items["line_items[1][quantity]"] = "1"
    base = settings.portal_base_url.rstrip("/")
    try:
        session = await _stripe(
            "POST",
            "/checkout/sessions",
            data={
                "mode": "subscription",
                **line_items,
                "metadata[app]": _APP_TAG,
                "subscription_data[metadata][app]": _APP_TAG,
                "success_url": f"{base}/billing/success",
                "cancel_url": f"{base}/pricing",
            },
        )
    except (StripeError, httpx.HTTPError) as e:
        logger.error("billing: could not create checkout session: %s", e)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE)
    return RedirectResponse(session["url"], status_code=status.HTTP_303_SEE_OTHER)


@router.get("/billing/success", response_class=HTMLResponse)
async def success(request: Request):
    _require_configured()
    # Deliberately static: the subscription is recorded by the webhook, never
    # from anything in this URL.
    return templates.TemplateResponse(request, "billing_success.html", {})


def verify_signature(payload: bytes, header: str, secret: str, now: float) -> bool:
    """Check a Stripe-Signature header (`t=<ts>,v1=<hex>[,v1=<hex>]`)."""
    timestamp = None
    candidates = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            candidates.append(value)
    if not timestamp or not timestamp.isdigit() or not candidates:
        return False
    if abs(now - int(timestamp)) > _WEBHOOK_TOLERANCE_SEC:
        return False
    expected = hmac.new(
        secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256
    ).hexdigest()
    return any(hmac.compare_digest(expected, c) for c in candidates)


def _period_end(subscription: dict) -> Optional[datetime]:
    # Newer Stripe API versions report the period on the item, older ones on
    # the subscription itself.
    ts = subscription.get("current_period_end")
    if ts is None:
        items = (subscription.get("items") or {}).get("data") or []
        ts = items[0].get("current_period_end") if items else None
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


def _price_id(subscription: dict) -> Optional[str]:
    items = (subscription.get("items") or {}).get("data") or []
    return (items[0].get("price") or {}).get("id") if items else None


@router.post("/billing/webhook")
async def webhook(request: Request):
    if not (_configured() and settings.stripe_webhook_secret):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    payload = await request.body()
    if not verify_signature(
        payload,
        request.headers.get("stripe-signature", ""),
        settings.stripe_webhook_secret,
        time.time(),
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST)
    try:
        event = json.loads(payload)
        event_type = event["type"]
        obj = event["data"]["object"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST)

    if (obj.get("metadata") or {}).get("app") != _APP_TAG:
        # Another product's event on a shared Stripe account.
        return {"ok": True}

    # The two event families arrive in no guaranteed order; each upsert fills
    # only the columns it knows, so either order ends in the same row.
    if event_type == "checkout.session.completed":
        if obj.get("mode") == "subscription" and obj.get("subscription"):
            await db.upsert_subscription(
                stripe_subscription_id=obj["subscription"],
                stripe_customer_id=obj.get("customer"),
                email=(obj.get("customer_details") or {}).get("email"),
            )
    elif event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        await db.upsert_subscription(
            stripe_subscription_id=obj["id"],
            stripe_customer_id=obj.get("customer"),
            status=obj.get("status"),
            price_id=_price_id(obj),
            current_period_end=_period_end(obj),
        )
        logger.info(
            "billing: subscription %s status=%s", obj["id"], obj.get("status")
        )
    # Every other event type is acknowledged and ignored.
    return {"ok": True}
