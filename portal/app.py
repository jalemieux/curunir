from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from portal import admin, db, sign_in


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    await db.run_migrations()
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(lifespan=lifespan)
app.include_router(sign_in.router)
app.include_router(admin.router)


@app.get("/healthz")
async def healthz():
    ok = await db.ping()
    return JSONResponse({"status": "ok" if ok else "degraded"})
