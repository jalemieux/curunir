import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from portal import admin, auth, db, sign_in, ws_agent, ws_browser


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    await db.run_migrations()
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(lifespan=lifespan)

_TOKEN_QS = re.compile(r"(\btoken=)[^&\s]+")


class _RedactingFilter(logging.Filter):
    """Replace `token=...` in any uvicorn-style access log message."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        record.msg = _TOKEN_QS.sub(r"\1<redacted>", msg)
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
