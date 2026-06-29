import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from portal import admin, auth, beta, db, sign_in, ws_agent, ws_browser
from portal.config import settings


def _configure_logging() -> None:
    """Attach a stream handler to the `portal` logger tree.

    uvicorn configures only its own loggers. Without this, every
    `logging.getLogger("portal.*")` call (routing, ws_agent, ws_browser, …)
    propagates to a handler-less root and is silently discarded — which is
    why session-routing events never showed up in `docker compose logs`.
    """
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    portal_logger = logging.getLogger("portal")
    portal_logger.handlers.clear()
    portal_logger.addHandler(handler)
    portal_logger.setLevel(level)
    # Leave propagate at its default (True): the root logger has no handler,
    # so records emit exactly once via the handler above. Propagation is what
    # lets pytest's caplog (a root handler) still capture portal logs.


_configure_logging()
logger = logging.getLogger(__name__)


async def _maybe_seed_dev_user() -> None:
    if not (settings.debug
            and settings.seed_user_email
            and settings.seed_container_token):
        return
    user = await db.upsert_user_with_container_token(
        settings.seed_user_email, settings.seed_container_token
    )
    logger.info(
        "dev seed: upserted user id=%s email=%s", user.id, user.email
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    await db.run_migrations()
    await _maybe_seed_dev_user()
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(lifespan=lifespan)

_TOKEN_QS = re.compile(r"(\btoken=)[^&\s]+")


class _RedactingFilter(logging.Filter):
    """Replace `token=...` in any uvicorn-style access log message.

    uvicorn.access uses AccessFormatter, which unpacks record.args as
    (client_addr, method, full_path, http_version, status_code) — preserve
    that shape and redact the path in place instead of clobbering args.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            record.args = (args[0], args[1], _TOKEN_QS.sub(r"\1<redacted>", args[2]), args[3], args[4])
        else:
            record.msg = _TOKEN_QS.sub(r"\1<redacted>", record.getMessage())
            record.args = ()
        return True


for _name in ("uvicorn.access", "uvicorn.error"):
    logging.getLogger(_name).addFilter(_RedactingFilter())

app.include_router(sign_in.router)
app.include_router(admin.router)
app.include_router(beta.router)
app.include_router(ws_agent.router)
app.include_router(ws_browser.router)


_STATIC_DIR = Path(__file__).parent / "static"
_LANDING_DIR = _STATIC_DIR / "landing"
_LAUNCH_DIR = _STATIC_DIR / "launch"
_FINANCE_DIR = _STATIC_DIR / "finance"

# Phones get the dedicated mobile UI (#304). The match is deliberately narrow —
# "Mobile" plus the major phone platforms — so tablets and desktops fall
# through to the full desktop SPA. It's only a convenience redirect; /m and
# /?desktop=1 are explicit escape hatches in both directions, so a bad UA match
# never traps a user.
_MOBILE_UA = re.compile(
    r"(iPhone|iPod|Android.*Mobile|Windows Phone|webOS|BlackBerry|"
    r"IEMobile|Opera Mini)",
    re.IGNORECASE,
)


def _is_mobile_ua(user_agent: str | None) -> bool:
    return bool(user_agent and _MOBILE_UA.search(user_agent))


@app.get("/")
async def root(request: Request, user=Depends(auth.optional_current_user)):
    if user is None:
        # Public landing page. The finance assistant is the primary public face
        # of curunir.ai, so unauthenticated `/` serves the finance landing; the
        # research assistant lives at /assistant (and the legacy /curunir alias).
        # Unauthenticated phone visitors stay here rather than being bounced to
        # /m (which 401s) — that would trap them with no next step.
        return FileResponse(_FINANCE_DIR / "index.html")
    # Authenticated: redirect phones to the mobile UI unless ?desktop=1 forces
    # the desktop SPA.
    if "desktop" not in request.query_params and _is_mobile_ua(
        request.headers.get("user-agent")
    ):
        return RedirectResponse("/m", status_code=307)
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/m")
async def mobile(user=Depends(auth.current_user)):
    """Mobile-first chat UI. Shares the /ws/browser backend with the desktop
    SPA — frontend split only. Gated by auth like the desktop chat surface."""
    return FileResponse(_STATIC_DIR / "mobile.html")


@app.get("/manifest.json")
async def manifest():
    # Served unauthenticated so add-to-homescreen works before the session
    # cookie is present.
    return FileResponse(
        _STATIC_DIR / "manifest.json", media_type="application/manifest+json"
    )


@app.get("/icon.svg")
async def icon():
    return FileResponse(_STATIC_DIR / "icon.svg", media_type="image/svg+xml")


@app.get("/healthz")
async def healthz():
    ok = await db.ping()
    return JSONResponse({"status": "ok" if ok else "degraded"})


# Research-assistant landing assets. Reports are mounted at /r/ so the in-page
# absolute links resolve regardless of which URL serves the page (the finance
# page reuses this same /r/ mount for its real memos). The research page is
# served at /assistant; /curunir/ is kept as a legacy alias for direct linking.
# The `reports` subdir is checked separately so a landing checkout without
# reports (e.g. a fresh dev clone) doesn't crash uvicorn at startup.
if _LANDING_DIR.exists():
    if (_LANDING_DIR / "reports").exists():
        app.mount("/r", StaticFiles(directory=_LANDING_DIR / "reports"), name="landing-reports")
    app.mount("/curunir", StaticFiles(directory=_LANDING_DIR, html=True), name="landing")
    app.mount("/assistant", StaticFiles(directory=_LANDING_DIR, html=True), name="assistant")

# Second landing for the GTM pipeline, aimed at solo operators (founders,
# solopreneurs, in-house marketers running it alone). Beta form posts to
# /beta/signup with source="launch" so admin can segment.
if _LAUNCH_DIR.exists():
    if (_LAUNCH_DIR / "reports").exists():
        app.mount(
            "/r-launch",
            StaticFiles(directory=_LAUNCH_DIR / "reports"),
            name="launch-reports",
        )
    app.mount("/launch", StaticFiles(directory=_LAUNCH_DIR, html=True), name="launch")

# Persona-specific landing for the finance assistant. Its in-page report
# links reuse the /r/ mount above (the real memos live in landing/reports),
# so no separate reports mount is needed. Beta form posts to /beta/signup
# with source="finance" so admin can segment.
if _FINANCE_DIR.exists():
    app.mount("/finance", StaticFiles(directory=_FINANCE_DIR, html=True), name="finance")
