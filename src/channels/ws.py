import asyncio
import http
import json
import logging
import os
import uuid
from typing import Callable
from urllib.parse import urlparse

import websockets
import websockets.exceptions

from src.channels._attachments import (
    _decode_attachments,
    _enrich_attachments,
    _stage_attachments,
)
from src.channels.base import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)

# Time the server waits for the client's first frame when a pairing token is
# required. Tight enough to make scanners give up, loose enough for a real
# client on a slow disk.
_HELLO_TIMEOUT_SEC = 3.0

# Default Origin allowlist. Browsers always send Origin on a WebSocket upgrade,
# so a missing Origin signals a native (non-browser) client like cli.py and is
# accepted. "null" is sent by sandboxed iframes / file:// pages and is allowed
# at the upgrade layer — the pairing token is the real defense for those.
_DEFAULT_LOCALHOST_ORIGINS: frozenset[str] = frozenset({
    "http://localhost",
    "http://127.0.0.1",
    "https://localhost",
    "https://127.0.0.1",
})


def _origin_allowed(origin: str | None, allowed_origins: frozenset[str]) -> bool:
    """Return True if *origin* is acceptable.

    Browsers always send Origin; native clients usually don't. We accept
    missing and ``"null"`` Origin headers — the pairing token covers those
    cases. For any other value we compare ``scheme://host`` (port-agnostic)
    against the allowlist so e.g. ``http://localhost:5173`` matches an
    allowlisted ``http://localhost``.
    """
    if origin is None or origin == "null":
        return True
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    if not parsed.scheme or not parsed.hostname:
        return False
    base = f"{parsed.scheme}://{parsed.hostname}"
    return base in allowed_origins


class WebSocketChannel:
    def __init__(
        self,
        in_queue: asyncio.Queue,
        host: str = "127.0.0.1",
        port: int = 8765,
        model: str = "",
        uploads_dir: str | None = None,
        cancel_session: Callable[[str], bool] | None = None,
        allowed_origins: frozenset[str] | set[str] | list[str] | None = None,
        pairing_token: str | None = None,
    ):
        self.in_queue = in_queue
        self.host = host
        self.port = port
        self.model = model
        self.uploads_dir = uploads_dir or os.path.join(os.getcwd(), "context", "uploads")
        self._connections: dict[str, websockets.ServerConnection] = {}
        self.cancel_session = cancel_session
        self._connection: websockets.ServerConnection | None = None
        self.allowed_origins: frozenset[str] = (
            frozenset(allowed_origins) if allowed_origins is not None
            else _DEFAULT_LOCALHOST_ORIGINS
        )
        self.pairing_token = pairing_token

    def _process_request(self, connection, request):
        """Reject cross-origin upgrades before the handler runs.

        Returning a Response from process_request aborts the handshake.
        """
        origin = request.headers.get("Origin")
        if not _origin_allowed(origin, self.allowed_origins):
            logger.warning(
                "Rejecting WebSocket upgrade from disallowed Origin %r", origin,
            )
            return connection.respond(http.HTTPStatus.FORBIDDEN, "origin not allowed\n")
        return None

    async def start(self) -> None:
        try:
            # max_size accommodates the 20 MB attachment batch cap after base64
            # expansion (~27 MB on the wire), plus headroom for the JSON envelope.
            async with websockets.serve(
                self._handle_connection, self.host, self.port,
                max_size=32 * 1024 * 1024,
                process_request=self._process_request,
            ) as server:
                logger.info("WebSocket server listening on %s:%d", self.host, self.port)
                await asyncio.get_running_loop().create_future()
        except asyncio.CancelledError:
            logger.info("WebSocket server shutting down")
            for conn in list(self._connections.values()):
                try:
                    await conn.close()
                except Exception:
                    pass
            raise

    async def _send_hello(
        self, websocket: websockets.ServerConnection, session_id: str
    ) -> None:
        # Bare "welcome" of older builds also carried `model`; we preserve
        # that field so the legacy CLI's "model: …" line still renders.
        payload: dict = {"type": "hello", "session_id": session_id}
        if self.model:
            payload["model"] = self.model
        try:
            await websocket.send(json.dumps(payload))
        except websockets.exceptions.ConnectionClosed:
            pass

    async def _rekey(
        self,
        websocket: websockets.ServerConnection,
        old_sid: str,
        new_sid: str,
    ) -> str:
        """Re-register *websocket* under *new_sid*, evicting any stale socket.

        Used when the client's hello frame names a prior session id it wants
        to resume. Returns the resulting session id (always *new_sid* on
        success; falls back to *old_sid* on a degenerate input).
        """
        if not isinstance(new_sid, str) or not new_sid:
            return old_sid
        if new_sid == old_sid:
            return old_sid

        existing = self._connections.get(new_sid)
        if existing is not None and existing is not websocket:
            logger.info(
                "Replacing stale connection for session %s with new client",
                new_sid,
            )
            try:
                await existing.close(1008, "Replaced by new connection")
            except Exception:
                pass

        if self._connections.get(old_sid) is websocket:
            del self._connections[old_sid]
        self._connections[new_sid] = websocket
        return new_sid

    async def _await_token_hello(
        self, websocket: websockets.ServerConnection,
    ) -> dict | None:
        """Wait for the client's first hello frame and validate its token.

        Returns the parsed hello dict on success. On failure (timeout, bad
        JSON, missing/wrong token), closes the socket with 1008 and returns
        None. Only called when ``self.pairing_token`` is set.
        """
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=_HELLO_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            logger.warning(
                "WebSocket pairing: no hello within %.1fs from %s; closing",
                _HELLO_TIMEOUT_SEC, websocket.remote_address,
            )
            await websocket.close(1008, "auth")
            return None
        except websockets.exceptions.ConnectionClosed:
            return None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "WebSocket pairing: first frame not JSON from %s; closing",
                websocket.remote_address,
            )
            await websocket.close(1008, "auth")
            return None

        if not isinstance(data, dict) or data.get("type") != "hello":
            logger.warning(
                "WebSocket pairing: first frame not a hello from %s; closing",
                websocket.remote_address,
            )
            await websocket.close(1008, "auth")
            return None

        if data.get("token") != self.pairing_token:
            logger.warning(
                "WebSocket pairing: bad/missing token from %s; closing",
                websocket.remote_address,
            )
            await websocket.close(1008, "auth")
            return None

        return data

    async def _handle_connection(self, websocket: websockets.ServerConnection) -> None:
        remote = websocket.remote_address
        session_id = uuid.uuid4().hex
        logger.info("Client connected from %s (session %s)", remote, session_id)

        resumed_sid: str | None = None
        if self.pairing_token is not None:
            hello = await self._await_token_hello(websocket)
            if hello is None:
                return
            requested = hello.get("session_id")
            if isinstance(requested, str) and requested:
                resumed_sid = requested

        self._connections[session_id] = websocket
        if resumed_sid is not None:
            new_sid = await self._rekey(websocket, session_id, resumed_sid)
            session_id = new_sid

        await self._send_hello(websocket, session_id)

        try:
            async for raw in websocket:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Received invalid JSON from client, ignoring")
                    continue

                if isinstance(data, dict) and data.get("type") == "hello":
                    new_sid = await self._rekey(
                        websocket, session_id, data.get("session_id"),
                    )
                    if new_sid != session_id:
                        session_id = new_sid
                        await self._send_hello(websocket, session_id)
                if data.get("command") == "interrupt":
                    delivered = bool(
                        self.cancel_session and self.cancel_session(session_id)
                    )
                    logger.info(
                        "Interrupt requested for cli session %s (delivered=%s)",
                        session_id, delivered,
                    )
                    continue

                if data.get("command") == "slash":
                    # Channels don't interpret slash — agent_worker does.
                    # Forward the raw text on the queue with command=slash.
                    await self.in_queue.put(IncomingMessage(
                        content=data.get("text", ""),
                        channel="cli",
                        session_id=session_id,
                        reply_address={},
                        command="slash",
                    ))
                    continue

                decoded, err = _decode_attachments(data.get("attachments"))
                if err is not None:
                    logger.info("Rejected inbound message: %s", err)
                    await self.send(OutgoingMessage(
                        content=f"Attachment rejected: {err}",
                        channel="cli",
                        session_id=session_id,
                        reply_address={},
                        final=True,
                    ))
                    continue

                await self._process_inbound(data, session_id)
        except websockets.exceptions.ConnectionClosedError:
            pass
        finally:
            if self._connections.get(session_id) is websocket:
                del self._connections[session_id]
            logger.info(
                "Client disconnected from %s (session %s)", remote, session_id
            )
            extract_msg = IncomingMessage(
                content="",
                channel="cli",
                session_id=session_id,
                reply_address={},
                command="extract",
            )
            await self.in_queue.put(extract_msg)

    async def _process_inbound(self, data: dict, session_id: str) -> None:
        decoded, err = _decode_attachments(data.get("attachments"))
        if err is not None:
            logger.info("Rejected inbound message: %s", err)
            await self.send(OutgoingMessage(
                content=f"Attachment rejected: {err}",
                channel="cli",
                session_id=session_id,
                reply_address={},
                final=True,
            ))
            return

        manifest = (
            _stage_attachments(decoded, session_id, self.uploads_dir)
            if decoded else None
        )

        msg = IncomingMessage(
            content=data.get("content", ""),
            channel="cli",
            session_id=session_id,
            reply_address={},
            command=data.get("command") or None,
            attachments=manifest,
        )
        await self.in_queue.put(msg)

    async def send(self, msg: OutgoingMessage) -> None:
        connection = self._connections.get(msg.session_id)
        if connection is None:
            logger.warning(
                "No WebSocket client connected for session %s; dropping outgoing message",
                msg.session_id,
            )
            return

        if msg.attachments:
            _enrich_attachments(msg.attachments, os.getcwd())

        payload: dict = {
            "content": msg.content,
            "tool_calls": msg.tool_calls,
            "final": msg.final,
            "delta": msg.delta,
            "attachments": msg.attachments if msg.attachments else None,
            "workflow": msg.workflow,
            "stats": msg.stats,
        }
        try:
            await connection.send(json.dumps(payload))
        except websockets.exceptions.ConnectionClosed:
            if self._connections.get(msg.session_id) is connection:
                del self._connections[msg.session_id]
            logger.warning("WebSocket connection closed while sending; message dropped")
