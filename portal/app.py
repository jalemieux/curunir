from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    # DB pool will be added in Task 2.
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return JSONResponse({"status": "ok"})
