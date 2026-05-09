import asyncio
import json
import logging
import os
import uuid
from typing import Callable

import websockets
import websockets.exceptions

from src.channels._attachments import (
    _decode_attachments,
    _enrich_attachments,
    _stage_attachments,
)
from src.channels.base import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class WebSocketChannel:
    def __init__(
        self,
        in_queue: asyncio.Queue,
        host: str = "0.0.0.0",
        port: int = 8765,
        model: str = "",
        uploads_dir: str | None = None,
        cancel_session: Callable[[str], bool] | None = None,
    ):
        self.in_queue = in_queue
        self.host = host
        self.port = port
        self.model = model
        self.uploads_dir = uploads_dir or os.path.join(os.getcwd(), "context", "uploads")
        self._connections: dict[str, websockets.ServerConnection] = {}
        self.cancel_session = cancel_session
        self._connection: websockets.ServerConnection | None = None

    async def start(self) -> None:
        try:
            # max_size accommodates the 20 MB attachment batch cap after base64
            # expansion (~27 MB on the wire), plus headroom for the JSON envelope.
            async with websockets.serve(
                self._handle_connection, self.host, self.port,
                max_size=32 * 1024 * 1024,
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

    async def _handle_connection(self, websocket: websockets.ServerConnection) -> None:
        remote = websocket.remote_address
        session_id = uuid.uuid4().hex
        self._connections[session_id] = websocket
        logger.info("Client connected from %s (session %s)", remote, session_id)

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
                    delivered = bool(self.cancel_session and self.cancel_session(SESSION_ID))
                    logger.info("Interrupt requested for cli session (delivered=%s)", delivered)
                    continue

                decoded, err = _decode_attachments(data.get("attachments"))
                if err is not None:
                    logger.info("Rejected inbound message: %s", err)
                    await self.send(OutgoingMessage(
                        content=f"Attachment rejected: {err}",
                        channel="cli",
                        session_id=SESSION_ID,
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
