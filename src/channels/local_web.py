"""LocalWebChannel — a loopback-bound web console served from the container.

A lightweight, operator-only web UI co-located with the agent. Unlike the
hosted Portal (a separate deployable that relays through a remote service),
this channel runs *inside* the container and reads the container-local stores
directly:

- ``GET /``               → the static SPA (reuses the portal frontend + wire protocol)
- ``GET /api/usage``      → token/cost rollup (``UsageStore.summary``)
- ``GET /api/portfolio``  → balance sheet (``portfolio.engine``)
- ``GET /api/schedules``  → cron tasks (``scheduler._load_tasks``)
- ``POST /api/schedules`` → create a cron task (``schedule_store.engine.create``)
- ``PUT /api/schedules/{id}`` → edit cron/prompt/skill/enabled (``engine.update``)
- ``POST /api/schedules/{id}/toggle`` → flip enabled (``engine.toggle``)
- ``DELETE /api/schedules/{id}`` → remove a task (``engine.delete``)
- ``GET /api/memory``     → ``context/memory/`` tree
- ``GET /api/memory/file``→ one memory file (path-traversal guarded)
- ``WS  /ws/browser``     → chat bridged straight into the agent queues

Security mirrors ``ws.py``: an Origin allowlist (loopback by default) plus the
shared ``context/.ws-token`` pairing token. The REST routes require the token
(``?token=`` query or ``X-Curunir-Token`` header); ``/ws/browser`` requires
both an allowed Origin and the token. Single-user, single-session by design —
no multiplexing, no Postgres, no sign-in.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable

import uvicorn
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.channels._attachments import (
    _decode_attachments,
    _enrich_attachments,
    _stage_attachments,
)
from src.channels.base import IncomingMessage, OutgoingMessage
from src.channels.ws import _DEFAULT_LOCALHOST_ORIGINS, _origin_allowed
from src.config import AgentConfig
from src.local_ui import readers
from src.schedule_store import db as sdb
from src.schedule_store import engine as sengine

logger = logging.getLogger(__name__)

#: Fixed session id for the local console. Single-user, single-session.
LOCAL_SESSION_ID = "local"

_STATIC_DIR = Path(__file__).resolve().parent.parent / "local_ui" / "static"


class LocalWebChannel:
    def __init__(
        self,
        in_queue: asyncio.Queue,
        config: AgentConfig,
        host: str = "127.0.0.1",
        port: int = 8766,
        model: str = "",
        persona: str = "",
        uploads_dir: str | None = None,
        cancel_session: Callable[[str], bool] | None = None,
        allowed_origins: frozenset[str] | set[str] | list[str] | None = None,
        pairing_token: str | None = None,
        history_provider: Callable[[str], list[dict]] | None = None,
        skills_provider: Callable[[], list[dict]] | None = None,
    ):
        self.in_queue = in_queue
        self.config = config
        self.host = host
        self.port = port
        self.model = model
        self.persona = persona
        self.uploads_dir = uploads_dir or os.path.join(
            os.getcwd(), "context", "uploads"
        )
        self.cancel_session = cancel_session
        self.allowed_origins: frozenset[str] = (
            frozenset(allowed_origins) if allowed_origins is not None
            else _DEFAULT_LOCALHOST_ORIGINS
        )
        self.pairing_token = pairing_token
        self.history_provider = history_provider or (lambda _sid: [])
        self.skills_provider = skills_provider or (lambda: [])
        # The single connected browser socket (single-session console).
        self._socket: WebSocket | None = None
        self.app = self._build_app()

    # --- auth helpers ------------------------------------------------------

    def _token_ok(self, supplied: str | None) -> bool:
        """True if the token gate is satisfied.

        A ``None`` pairing token disables the gate entirely (mirrors ws.py,
        used in tests and local dev). Otherwise the supplied value must match.
        """
        if self.pairing_token is None:
            return True
        return supplied == self.pairing_token

    def _rest_token(self, request: Request) -> str | None:
        return request.headers.get("X-Curunir-Token") or request.query_params.get(
            "token"
        )

    def _schedules_db(self) -> str:
        """Initialize (if needed) and return the schedule store path.

        Mirrors ``schedule_tool._db`` so writes go through the same engine the
        ``schedule`` tool and scheduler use — no separate query/validation path.
        """
        path = str(self.config.schedules_db)
        sdb.init_db(path)
        return path

    # --- app construction --------------------------------------------------

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        if _STATIC_DIR.is_dir():
            app.mount(
                "/static", StaticFiles(directory=str(_STATIC_DIR)), name="static"
            )

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(str(_STATIC_DIR / "index.html"))

        @app.get("/api/usage")
        async def api_usage(
            request: Request,
            window: str = "7d",
            by: str = "model",
        ) -> JSONResponse:
            if not self._token_ok(self._rest_token(request)):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            try:
                return JSONResponse(readers.usage_summary(self.config, window, by))
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)

        @app.get("/api/portfolio")
        async def api_portfolio(request: Request) -> JSONResponse:
            if not self._token_ok(self._rest_token(request)):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return JSONResponse(readers.portfolio_overview(self.config))

        @app.get("/api/schedules")
        async def api_schedules(request: Request) -> JSONResponse:
            if not self._token_ok(self._rest_token(request)):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return JSONResponse(readers.schedules(self.config))

        @app.post("/api/schedules")
        async def api_schedule_create(request: Request) -> JSONResponse:
            if not self._token_ok(self._rest_token(request)):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            body = await request.json()
            fields = {
                k: body.get(k) for k in ("id", "cron", "prompt", "skill")
            }
            fields["enabled"] = bool(body.get("enabled", True))
            try:
                row = sengine.create(
                    self._schedules_db(), fields,
                    skill_allowlist=self.config.skill_allowlist,
                )
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            return JSONResponse(row)

        @app.put("/api/schedules/{task_id}")
        async def api_schedule_update(
            task_id: str, request: Request
        ) -> JSONResponse:
            if not self._token_ok(self._rest_token(request)):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            body = await request.json()
            fields = {
                k: body[k] for k in ("cron", "prompt", "skill", "enabled")
                if k in body
            }
            if "enabled" in fields:
                fields["enabled"] = bool(fields["enabled"])
            try:
                row = sengine.update(
                    self._schedules_db(), task_id, fields,
                    skill_allowlist=self.config.skill_allowlist,
                )
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            return JSONResponse(row)

        @app.post("/api/schedules/{task_id}/toggle")
        async def api_schedule_toggle(
            task_id: str, request: Request
        ) -> JSONResponse:
            if not self._token_ok(self._rest_token(request)):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            try:
                row = sengine.toggle(self._schedules_db(), task_id)
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            return JSONResponse(row)

        @app.delete("/api/schedules/{task_id}")
        async def api_schedule_delete(
            task_id: str, request: Request
        ) -> JSONResponse:
            if not self._token_ok(self._rest_token(request)):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            try:
                sengine.delete(self._schedules_db(), task_id)
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            return JSONResponse({"ok": True, "id": task_id})

        @app.get("/api/memory")
        async def api_memory(request: Request) -> JSONResponse:
            if not self._token_ok(self._rest_token(request)):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return JSONResponse(readers.memory_tree(self.config))

        @app.get("/api/memory/file")
        async def api_memory_file(
            request: Request, path: str = Query(...)
        ) -> JSONResponse:
            if not self._token_ok(self._rest_token(request)):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            try:
                return JSONResponse(readers.memory_file(self.config, path))
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            except FileNotFoundError as e:
                return JSONResponse({"error": str(e)}, status_code=404)

        @app.websocket("/ws/browser")
        async def ws_browser(ws: WebSocket) -> None:
            await self._serve_browser(ws)

        return app

    # --- websocket ---------------------------------------------------------

    async def _serve_browser(self, ws: WebSocket) -> None:
        origin = ws.headers.get("origin")
        if not _origin_allowed(origin, self.allowed_origins):
            logger.warning("Rejecting local-web WS from disallowed Origin %r", origin)
            await ws.close(code=4403, reason="origin")
            return
        if not self._token_ok(ws.query_params.get("token")):
            logger.warning("Rejecting local-web WS: bad/missing token")
            await ws.close(code=4401, reason="auth")
            return

        await ws.accept()
        self._socket = ws
        logger.info("Local web console connected")
        await ws.send_text(json.dumps({"type": "agent_status", "status": "online"}))
        # Surface the active model/persona so the console header can label them.
        await ws.send_text(json.dumps(
            {"type": "meta", "model": self.model, "persona": self.persona}
        ))

        async def respond(frame: dict) -> None:
            await ws.send_text(json.dumps(frame))

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Local web console sent invalid JSON; ignoring")
                    continue
                await self._handle_inbound_frame(payload, respond=respond)
        except WebSocketDisconnect:
            pass
        finally:
            if self._socket is ws:
                self._socket = None
            logger.info("Local web console disconnected")
            # Mirror ws.py: flush the conversation into memory extraction.
            await self.in_queue.put(IncomingMessage(
                content="", channel="local_web", session_id=LOCAL_SESSION_ID,
                reply_address={}, command="extract",
            ))

    async def _handle_inbound_frame(
        self,
        payload: dict,
        respond: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        """Dispatch one browser frame.

        Control frames (interrupt, history/skills/conversations requests,
        slash) are handled inline; chat frames are staged and enqueued as an
        ``IncomingMessage`` for the agent worker. ``respond`` sends a frame
        back to the browser (used for snapshots); it is None in headless
        unit tests that only assert enqueue behavior.
        """
        command = payload.get("command")

        if command == "interrupt":
            delivered = bool(
                self.cancel_session and self.cancel_session(LOCAL_SESSION_ID)
            )
            logger.info(
                "Interrupt requested for local session (delivered=%s)", delivered
            )
            return

        if command == "history_request":
            if respond is not None:
                messages = self.history_provider(LOCAL_SESSION_ID)
                for m in messages:
                    if m.get("attachments"):
                        _enrich_attachments(m["attachments"], os.getcwd())
                await respond({
                    "type": "history_snapshot",
                    "session_id": LOCAL_SESSION_ID,
                    "messages": messages,
                })
            return

        if command == "skills_request":
            if respond is not None:
                await respond({
                    "type": "skills_snapshot",
                    "session_id": LOCAL_SESSION_ID,
                    "skills": self.skills_provider(),
                })
            return

        if command == "slash":
            await self.in_queue.put(IncomingMessage(
                content=payload.get("text", ""),
                channel="local_web",
                session_id=LOCAL_SESSION_ID,
                reply_address={},
                command="slash",
            ))
            return

        decoded, err = _decode_attachments(payload.get("attachments"))
        if err is not None:
            logger.info("Rejected inbound message: %s", err)
            await self.send(OutgoingMessage(
                content=f"Attachment rejected: {err}",
                channel="local_web",
                session_id=LOCAL_SESSION_ID,
                reply_address={},
                final=True,
            ))
            return

        manifest = (
            _stage_attachments(decoded, LOCAL_SESSION_ID, self.uploads_dir)
            if decoded else None
        )
        await self.in_queue.put(IncomingMessage(
            content=payload.get("content", ""),
            channel="local_web",
            session_id=LOCAL_SESSION_ID,
            reply_address={},
            command=command or None,
            attachments=manifest,
        ))

    # --- channel protocol --------------------------------------------------

    async def start(self) -> None:
        config = uvicorn.Config(
            self.app, host=self.host, port=self.port, log_level="warning"
        )
        server = uvicorn.Server(config)
        logger.info("Local web console listening on %s:%d", self.host, self.port)
        await server.serve()

    async def send(self, msg: OutgoingMessage) -> None:
        ws = self._socket
        if ws is None:
            return
        if msg.attachments:
            _enrich_attachments(msg.attachments, os.getcwd())
        frame = {
            "session_id": msg.session_id,
            "content": msg.content,
            "tool_calls": msg.tool_calls,
            "final": msg.final,
            "delta": msg.delta,
            "attachments": msg.attachments if msg.attachments else None,
            "workflow": msg.workflow,
            "stats": msg.stats,
        }
        try:
            await ws.send_text(json.dumps(frame))
        except (WebSocketDisconnect, RuntimeError):
            if self._socket is ws:
                self._socket = None
            logger.warning("Local web console closed while sending; message dropped")
