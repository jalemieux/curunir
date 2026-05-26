"""Public beta-signup endpoint.

Captures email addresses (and an optional one-line message) from the
landing page. Idempotent on email — re-submitting the same address
returns the existing record without raising.

Rate-limited per-IP using the same in-memory bucket pattern as sign_in.
"""

import logging
import re
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from portal import db
from portal.config import settings


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NO_HTML_META_RE = re.compile(r'[<>&"\']')
_IP_SHAPE_RE = re.compile(r"^[0-9a-fA-F:.]{1,45}$")


logger = logging.getLogger(__name__)

router = APIRouter()


# Per-IP rate limit (mirrors sign_in.py).
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


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        first = fwd.split(",")[0].strip()
        return first if _IP_SHAPE_RE.match(first) else "unknown"
    return request.client.host if request.client else "unknown"


class BetaSignupIn(BaseModel):
    email: str = Field(..., max_length=320)
    message: Optional[str] = Field(default=None, max_length=2000)
    source: Optional[str] = Field(default=None, max_length=64)

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("invalid email")
        if _NO_HTML_META_RE.search(v):
            raise ValueError("invalid characters")
        return v

    @field_validator("message", "source")
    @classmethod
    def _no_html_meta(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and _NO_HTML_META_RE.search(v):
            raise ValueError("invalid characters")
        return v


@router.post("/beta/signup")
async def beta_signup(payload: BetaSignupIn, request: Request):
    ip = _client_ip(request)
    if _rate_limited(ip):
        return JSONResponse(
            {"ok": False, "error": "rate_limited"},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    user_agent = request.headers.get("user-agent", "")[:512] or None
    source = (payload.source or "landing")[:64]

    _, created = await db.create_beta_signup(
        email=str(payload.email),
        message=payload.message,
        source=source,
        ip=ip,
        user_agent=user_agent,
    )

    logger.info(
        "beta signup: email=%s source=%s created=%s ip=%s",
        payload.email, source, created, ip,
    )

    return JSONResponse({"ok": True, "created": created})
