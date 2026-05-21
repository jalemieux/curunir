import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from portal import admin, auth, db, sign_in, ws_agent, ws_browser
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
    if settings.is_local_mode:
        # Local profile: seed the single env-defined user instead of
        # provisioning via magic-link/admin. Its id is stashed on app.state
        # so `/` can mint the session cookie for it.
        user = await db.ensure_local_user()
        app.state.local_user_id = user.id
        logger.info(
            "local mode: seeded user id=%s email=%s", user.id, user.email
        )
    else:
        await _maybe_seed_dev_user()
    try:
        yield
    finally:
        await db.close_pool()


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


def create_app() -> FastAPI:
    """Build the portal FastAPI app.

    In hosted mode (default) the magic-link sign-in and admin routers are
    mounted. In local mode (``PORTAL_MODE=local``) both are omitted: the
    local browser surface has no per-request auth and `/` auto-issues the
    session cookie for the env-seeded local user. See portal/README.md.
    """
    app = FastAPI(lifespan=lifespan)

    if not settings.is_local_mode:
        app.include_router(sign_in.router)
        app.include_router(admin.router)
    app.include_router(ws_agent.router)
    app.include_router(ws_browser.router)

    @app.get("/healthz")
    async def healthz():
        ok = await db.ping()
        return JSONResponse({"status": "ok" if ok else "degraded"})

    @app.get("/")
    async def root(request: Request, user=Depends(auth.optional_current_user)):
        index = Path(__file__).parent / "static" / "index.html"
        if settings.is_local_mode:
            # No per-request auth in local mode — serve the chat UI directly.
            # On the first visit there's no cookie yet; mint one for the
            # seeded local user so the browser WebSocket can authenticate.
            response = FileResponse(index)
            if user is None:
                auth.set_local_session_cookie(
                    response, request.app.state.local_user_id
                )
            return response
        if user is None:
            return RedirectResponse("/needs-invite", status_code=302)
        return FileResponse(index)

    return app


app = create_app()
