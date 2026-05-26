import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from portal import admin, auth, beta, db, research, sign_in, ws_agent, ws_browser
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
app.include_router(research.router)
app.include_router(ws_agent.router)
app.include_router(ws_browser.router)

# Static assets for the research section (CSS + OG images).
_RESEARCH_STATIC_DIR = Path(__file__).parent / "static" / "research"
if _RESEARCH_STATIC_DIR.is_dir():
    app.mount(
        "/static/research",
        StaticFiles(directory=_RESEARCH_STATIC_DIR),
        name="research-static",
    )


_LANDING_DIR = Path(__file__).parent / "static" / "landing"
_LAUNCH_DIR = Path(__file__).parent / "static" / "launch"


@app.get("/")
async def root(user=Depends(auth.optional_current_user)):
    if user is None:
        # Public landing page — same file served at /curunir/ for direct linking.
        return FileResponse(_LANDING_DIR / "index.html")
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/healthz")
async def healthz():
    ok = await db.ping()
    return JSONResponse({"status": "ok" if ok else "degraded"})


# Public landing page assets. Reports are mounted at /r/ so the in-page
# absolute links resolve regardless of which URL serves the page.
# /curunir/ is also kept as a stable alias for direct linking. The
# `reports` subdir is checked separately so a landing checkout without
# reports (e.g. a fresh dev clone) doesn't crash uvicorn at startup.
if _LANDING_DIR.exists():
    if (_LANDING_DIR / "reports").exists():
        app.mount("/r", StaticFiles(directory=_LANDING_DIR / "reports"), name="landing-reports")
    app.mount("/curunir", StaticFiles(directory=_LANDING_DIR, html=True), name="landing")

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
