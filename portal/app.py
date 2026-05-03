import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from portal import admin, auth, db, sign_in, ws_agent, ws_browser
from portal.routing import routing


logger = logging.getLogger(__name__)


_SHUTDOWN_FRAME = json.dumps({"type": "shutdown"})


async def _shutdown_agents() -> None:
    """Best-effort: tell every connected agent we're going down, then close.

    Agents that recognise `{"type":"shutdown"}` will reconnect immediately
    rather than wait for their heartbeat-timeout to fire. A failure on one
    socket must not block the others — portal shutdown is time-sensitive.
    """
    for agent in routing.all_agents():
        try:
            await agent.send_text(_SHUTDOWN_FRAME)
        except Exception:
            logger.warning("shutdown frame send failed", exc_info=True)
        try:
            await agent.close(code=1012, reason="portal shutdown")
        except Exception:
            logger.warning("agent close failed during shutdown", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    await db.run_migrations()
    try:
        yield
    finally:
        await _shutdown_agents()
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
app.include_router(ws_agent.router)
app.include_router(ws_browser.router)


@app.get("/healthz")
async def healthz():
    ok = await db.ping()
    return JSONResponse({"status": "ok" if ok else "degraded"})


@app.get("/")
async def root(user=Depends(auth.optional_current_user)):
    if user is None:
        return RedirectResponse("/needs-invite", status_code=302)
    return FileResponse(Path(__file__).parent / "static" / "index.html")
