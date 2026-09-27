"""First-party page-view / CTA-click tracking for positioning-test variants.

The top of the funnel for /v/<slug>/ pages (#547): sign-ups per variant are
already in beta_signups.source; this records views and CTA clicks so
conversion (signups ÷ views) is computable. No cookies, no third-party
scripts, no raw IPs — the client IP is stored only as a keyed hash.

Rate-limited per-IP using the same in-memory bucket pattern as beta signup.
"""

import hashlib
import hmac
import logging
import time
from collections import defaultdict, deque
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from portal import db
from portal.beta import _client_ip
from portal.config import settings


logger = logging.getLogger(__name__)

router = APIRouter()


# Separate bucket from /beta/signup so page views can't starve a sign-up.
_rate_buckets: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=64))


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    bucket = _rate_buckets[ip]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= settings.rate_limit_per_min:
        return True
    bucket.append(now)
    return False


def hash_ip(ip: str) -> str:
    """HMAC-SHA256 of the IP, keyed with the portal secret.

    A bare SHA-256 of an IPv4 address is reversible by enumerating the
    2^32 space; keying it keeps the hash stable for dedup but unreversible
    without the secret.
    """
    return hmac.new(
        settings.portal_secret_key.encode(), ip.encode(), hashlib.sha256
    ).hexdigest()


class TrackIn(BaseModel):
    # Same shape as a variant slug (see the gtm-smoke-test landing playbook).
    variant: str = Field(..., pattern=r"^[a-z0-9-]{1,40}$")
    event_type: Literal["page_view", "cta_click"]


@router.post("/track")
async def track(payload: TrackIn, request: Request):
    ip = _client_ip(request)
    if _rate_limited(ip):
        return JSONResponse(
            {"ok": False, "error": "rate_limited"},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    user_agent = request.headers.get("user-agent", "")[:512] or None
    await db.create_variant_event(
        variant=payload.variant,
        event_type=payload.event_type,
        ip_hash=hash_ip(ip),
        user_agent=user_agent,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
