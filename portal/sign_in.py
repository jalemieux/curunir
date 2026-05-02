import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from portal import auth, db
from portal.config import settings


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# In-memory rate limit: per-IP deque of timestamps (seconds).
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
    # Render terminates TLS and forwards via proxy; honor X-Forwarded-For first hop.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _no_cache_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
    }


@router.get("/sign-in", response_class=HTMLResponse)
async def sign_in_get(request: Request, token: str = ""):
    if _rate_limited(_client_ip(request)):
        return HTMLResponse(
            "Too many attempts. Try again in a minute.",
            status_code=429,
            headers=_no_cache_headers(),
        )
    user = await db.get_active_user_by_sign_in_token(token)
    if user is None:
        return templates.TemplateResponse(
            request, "sign_in_error.html", {}, status_code=400,
            headers=_no_cache_headers(),
        )
    return templates.TemplateResponse(
        request, "sign_in_confirm.html",
        {"email": user.email, "token": token},
        headers=_no_cache_headers(),
    )


@router.post("/sign-in")
async def sign_in_post(request: Request, token: str = Form(...)):
    if _rate_limited(_client_ip(request)):
        return HTMLResponse("Too many attempts.", status_code=429)
    user = await db.get_active_user_by_sign_in_token(token)
    if user is None:
        return templates.TemplateResponse(
            request, "sign_in_error.html", {}, status_code=400,
            headers=_no_cache_headers(),
        )
    cookie_value = auth.sign_session(user.id)
    response = RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=auth.SESSION_COOKIE,
        value=cookie_value,
        secure=not settings.debug,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


@router.get("/needs-invite", response_class=HTMLResponse)
async def needs_invite(request: Request):
    return templates.TemplateResponse(request, "needs_invite.html", {})
