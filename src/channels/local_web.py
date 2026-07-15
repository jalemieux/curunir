"""LocalWebChannel — a loopback-bound web console served from the container.

A lightweight, operator-only web UI co-located with the agent. Unlike the
hosted Portal (a separate deployable that relays through a remote service),
this channel runs *inside* the container and reads the container-local stores
directly:

- ``GET /``               → the static SPA (reuses the portal frontend + wire protocol)
- ``GET /api/usage``      → token/cost rollup (``UsageStore.summary``)
- ``GET /api/portfolio``  → balance sheet (``portfolio.engine``)
- ``GET /api/crm``        → leads + pipeline (``crm.engine``)
- ``GET /api/schedules``  → cron tasks (``scheduler._load_tasks``)
- ``POST /api/schedules`` → create a cron task (``schedule_store.engine.create``)
- ``PUT /api/schedules/{id}`` → edit cron/prompt/skill/enabled (``engine.update``)
- ``POST /api/schedules/{id}/toggle`` → flip enabled (``engine.toggle``)
- ``DELETE /api/schedules/{id}`` → remove a task (``engine.delete``)
- ``GET /api/memory``     → ``context/memory/`` tree
- ``GET /api/memory/file``→ one memory file (path-traversal guarded)
- ``GET /api/files``      → ``context/workspace/generated/`` deliverables listing
- ``GET /api/files/download`` → stream one deliverable (path-traversal guarded)
- ``WS  /ws/browser``     → chat bridged straight into the agent queues

Security mirrors ``ws.py``: an Origin allowlist (loopback by default) plus the
shared ``context/.ws-token`` pairing token. The REST routes require the token
(``?token=`` query or ``X-Curunir-Token`` header); ``/ws/browser`` requires
both an allowed Origin and the token. Single-user, single-socket — but the
socket drives many conversations: ``/ws/browser`` resolves ``session_id``
per-frame (``payload.session_id`` → ``LOCAL_SESSION_ID``), so the SPA's
conversation sidebar can list/switch/create/delete conversations the same way
the portal does. A ``conversations_request`` is answered with a
``conversations_snapshot``; delete routes through the existing ``clear``
command (extract-then-delete) rather than a raw store delete. No multiplexing,
no Postgres, no sign-in.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
from collections import deque
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
from src.modules import enabled_modules
from src.schedule_store import db as sdb
from src.schedule_store import engine as sengine

logger = logging.getLogger(__name__)

#: Fixed session id for the local console. Single-user, single-session.
LOCAL_SESSION_ID = "local"

#: Bound on the recent-``client_msg_id`` dedup ledger (see ``_seen_msg``). The
#: browser buffers durable frames and replays them on reconnect, so the same
#: frame can arrive twice; this keeps a small window of recently-seen ids to
#: drop replays without letting the ledger grow unbounded.
_RECENT_MSG_CAP = 256

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
        conversations_provider: Callable[[], list[dict]] | None = None,
        ingest: Callable[[str], "asyncio.Future | object"] | None = None,
        doc_card_min_bytes: int = 50_000,
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
        # Persona-gated UI modules (tab + read endpoints), derived once from
        # the persona's skill allowlist. None/empty allowlist → no modules.
        self._modules = enabled_modules(self.config.skill_allowlist)
        self._module_panels = {m.panel_id for m in self._modules}
        self.history_provider = history_provider or (lambda _sid: [])
        self.skills_provider = skills_provider or (lambda: [])
        self.conversations_provider = conversations_provider or (lambda: [])
        # The single connected browser socket (single-session console).
        self._socket: WebSocket | None = None
        # Bounded recent-``client_msg_id`` ledger for idempotent replay dedup.
        self._recent_msg_ids: set[str] = set()
        self._recent_msg_order: deque[str] = deque()
        # Eager document ingestion (docs/document-ingestion.md): async callable
        # path -> card text, wired to src.document_ingest.ingest_document in
        # run.py. None disables ingestion (uploads still stage, all `skipped`).
        # Task refs are held so a running ingestion can't be garbage-collected
        # mid-flight (same failure class as the scheduler's #500).
        self._ingest = ingest
        self.doc_card_min_bytes = doc_card_min_bytes
        self._ingest_tasks: set[asyncio.Task] = set()
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

    def _module_enabled(self, panel_id: str) -> bool:
        """True if the named UI module is owned by the active persona."""
        return panel_id in self._module_panels

    def _seen_msg(self, client_msg_id: str | None) -> bool:
        """Record a ``client_msg_id`` and report whether it was already seen.

        The browser buffers durable chat/slash frames and replays them on
        reconnect (the outbound delivery guarantee), so the same frame can be
        received twice. This bounded ledger drops the replay so a message
        isn't processed twice. A missing/empty id is never deduped — a client
        that doesn't stamp ids keeps the old at-most-once-per-send behavior.
        """
        if not client_msg_id:
            return False
        if client_msg_id in self._recent_msg_ids:
            return True
        self._recent_msg_ids.add(client_msg_id)
        self._recent_msg_order.append(client_msg_id)
        if len(self._recent_msg_order) > _RECENT_MSG_CAP:
            self._recent_msg_ids.discard(self._recent_msg_order.popleft())
        return False

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
            if not self._module_enabled("portfolio"):
                return JSONResponse({"error": "not found"}, status_code=404)
            return JSONResponse(readers.portfolio_overview(self.config))

        @app.get("/api/crm")
        async def api_crm(request: Request) -> JSONResponse:
            if not self._token_ok(self._rest_token(request)):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            if not self._module_enabled("crm"):
                return JSONResponse({"error": "not found"}, status_code=404)
            return JSONResponse(readers.crm_overview(self.config))

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

        @app.get("/api/files")
        async def api_files(request: Request) -> JSONResponse:
            if not self._token_ok(self._rest_token(request)):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return JSONResponse(readers.generated_files(self.config))

        @app.get("/api/files/download")
        async def api_files_download(
            request: Request, path: str = Query(...)
        ):
            if not self._token_ok(self._rest_token(request)):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            try:
                target = readers.generated_file_path(self.config, path)
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            except FileNotFoundError as e:
                return JSONResponse({"error": str(e)}, status_code=404)
            mime, _ = mimetypes.guess_type(target.name)
            return FileResponse(
                str(target),
                filename=target.name,
                media_type=mime or "application/octet-stream",
                content_disposition_type="attachment",
            )

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
            {
                "type": "meta",
                "model": self.model,
                "persona": self.persona,
                "modules": [m.panel_id for m in self._modules],
            }
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

        The session id is resolved per-frame (``payload.session_id`` →
        ``LOCAL_SESSION_ID``), so one browser socket drives many conversations
        — the sidebar switches/creates/deletes by sending a different
        ``session_id``. Snapshot frames echo the requested ``sid`` so the chat
        module's per-session filter accepts them.
        """
        command = payload.get("command")
        sid = payload.get("session_id") or LOCAL_SESSION_ID

        if command == "interrupt":
            delivered = bool(self.cancel_session and self.cancel_session(sid))
            logger.info(
                "Interrupt requested for local session %s (delivered=%s)",
                sid, delivered,
            )
            return

        if command == "history_request":
            if respond is not None:
                messages = self.history_provider(sid)
                for m in messages:
                    if m.get("attachments"):
                        _enrich_attachments(m["attachments"], os.getcwd())
                await respond({
                    "type": "history_snapshot",
                    "session_id": sid,
                    "messages": messages,
                })
            return

        if command == "skills_request":
            if respond is not None:
                await respond({
                    "type": "skills_snapshot",
                    "session_id": sid,
                    "skills": self.skills_provider(),
                })
            return

        if command == "conversations_request":
            if respond is not None:
                await respond({
                    "type": "conversations_snapshot",
                    "session_id": sid,
                    "conversations": self.conversations_provider(),
                })
            return

        if command == "upload":
            await self._handle_upload_frame(payload, sid, respond)
            return

        if command == "slash":
            if self._seen_msg(payload.get("client_msg_id")):
                return
            await self.in_queue.put(IncomingMessage(
                content=payload.get("text", ""),
                channel="local_web",
                session_id=sid,
                reply_address={},
                command="slash",
            ))
            return

        if self._seen_msg(payload.get("client_msg_id")):
            return

        staged_manifest, staged_err = self._resolve_staged_files(
            payload.get("staged_files")
        )
        if staged_err is not None:
            logger.info("Rejected inbound message: %s", staged_err)
            await self.send(OutgoingMessage(
                content=f"Attachment rejected: {staged_err}",
                channel="local_web",
                session_id=sid,
                reply_address={},
                final=True,
            ))
            return

        decoded, err = _decode_attachments(payload.get("attachments"))
        if err is not None:
            logger.info("Rejected inbound message: %s", err)
            await self.send(OutgoingMessage(
                content=f"Attachment rejected: {err}",
                channel="local_web",
                session_id=sid,
                reply_address={},
                final=True,
            ))
            return

        manifest = list(staged_manifest)
        if decoded:
            manifest.extend(_stage_attachments(decoded, sid, self.uploads_dir))
        await self.in_queue.put(IncomingMessage(
            content=payload.get("content", ""),
            channel="local_web",
            session_id=sid,
            reply_address={},
            command=command or None,
            attachments=manifest or None,
        ))

    # --- document upload + eager ingestion ----------------------------------

    def _resolve_staged_files(
        self, staged: list | None
    ) -> tuple[list[dict], str | None]:
        """Turn client-supplied staged-file refs into a validated manifest.

        The browser echoes back the ``path`` entries it received in an
        ``upload_result``, so every path must resolve inside ``uploads_dir``
        (``.resolve()`` collapses symlinks — same guard as the REST file
        readers). Returns (manifest, None) or ([], error).
        """
        if not staged:
            return [], None
        if not isinstance(staged, list):
            return [], "staged_files must be a list"
        uploads_root = Path(self.uploads_dir).resolve()
        manifest: list[dict] = []
        for i, item in enumerate(staged):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                return [], f"staged_files[{i}] missing or invalid 'path'"
            path = Path(item["path"]).resolve()
            if not path.is_relative_to(uploads_root):
                return [], f"staged_files[{i}] is outside the uploads directory"
            if not path.is_file():
                return [], f"staged_files[{i}] not found: {item['path']}"
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            manifest.append({
                "filename": item.get("filename") or path.name,
                "path": str(path),
                "mime_type": mime,
                "size": path.stat().st_size,
            })
        return manifest, None

    def _ingest_eligible(self, entry: dict) -> bool:
        """Card-worthy: a non-image document at or above the size threshold."""
        if self._ingest is None:
            return False
        if entry["mime_type"].startswith("image/"):
            return False
        return entry["size"] >= self.doc_card_min_bytes

    async def _handle_upload_frame(self, payload: dict, sid: str, respond) -> None:
        """Stage an upload batch and eagerly ingest eligible documents.

        Responds with an ``upload_result`` manifest (per-file ingest status:
        ``pending``/``skipped``), then fires one background ingestion task per
        pending file; each completion emits a ``document_card`` frame
        (``ok``/``error``) that the SPA uses to unblock the composer. Nothing
        is enqueued for the agent — the document enters the conversation with
        the user's eventual message, which references the staged paths.
        """
        upload_id = payload.get("upload_id")

        async def _respond(frame: dict) -> None:
            if respond is not None:
                await respond(frame)

        decoded, err = _decode_attachments(payload.get("attachments"))
        if err is not None:
            logger.info("Rejected upload %s: %s", upload_id, err)
            await _respond({
                "type": "upload_result", "upload_id": upload_id, "error": err,
            })
            return

        manifest = _stage_attachments(decoded, sid, self.uploads_dir)
        files = [
            {**entry, "ingest": "pending" if self._ingest_eligible(entry) else "skipped"}
            for entry in manifest
        ]
        await _respond({
            "type": "upload_result", "upload_id": upload_id, "files": files,
        })

        for entry in files:
            if entry["ingest"] != "pending":
                continue
            task = asyncio.create_task(
                self._ingest_and_notify(entry, upload_id, _respond)
            )
            self._ingest_tasks.add(task)
            task.add_done_callback(self._ingest_tasks.discard)

    async def _ingest_and_notify(self, entry: dict, upload_id, respond) -> None:
        """Run one document ingestion and report the outcome to the browser.

        Never raises: an ingestion failure becomes a ``status: error`` frame
        (the SPA unblocks and the message falls back to the raw-document
        path), and a notify failure is logged — the card is already on disk,
        so the agent-side card lookup still works.
        """
        frame = {
            "type": "document_card",
            "upload_id": upload_id,
            "path": entry["path"],
            "filename": entry["filename"],
        }
        try:
            await self._ingest(entry["path"])
            frame["status"] = "ok"
        except Exception as exc:  # noqa: BLE001 — failure must reach the UI
            logger.warning("Ingestion failed for %s: %s", entry["path"], exc)
            frame["status"] = "error"
            frame["error"] = str(exc)
        try:
            await respond(frame)
        except Exception as exc:  # noqa: BLE001 — socket may be gone
            logger.warning("Could not deliver document_card frame: %s", exc)

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
