import asyncio
import base64
import json
import logging

import websockets
import websockets.exceptions

from src.channels.base import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)

SESSION_ID = "cli"

# Size caps (mirrored in cli.py)
_MAX_IMAGE_BYTES = 5 * 1024 * 1024          # 5 MB
_MAX_TEXT_BYTES = 256 * 1024                # 256 KB
_MAX_TOTAL_BYTES = 20 * 1024 * 1024         # 20 MB
_ALLOWED_IMAGE_MIMES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
})


def _decode_attachments(raw: list | None) -> tuple[list[dict] | None, str | None]:
    """Validate and base64-decode inbound attachment payloads.

    Returns (decoded_items, None) on success, or (None, error_str) on failure.
    A decoded item is {"filename": str, "mime_type": str, "bytes": bytes}.
    No disk I/O here — callers stage the bytes separately.
    """
    if raw is None:
        return [], None
    if not isinstance(raw, list):
        return None, "attachments must be a list"

    decoded: list[dict] = []
    total_bytes = 0
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return None, f"attachment[{i}] is not an object"
        for key in ("filename", "mime_type", "data"):
            if key not in item or not isinstance(item[key], str):
                return None, f"attachment[{i}] missing or invalid '{key}'"

        filename = item["filename"]
        mime = item["mime_type"]
        try:
            payload = base64.b64decode(item["data"], validate=True)
        except (ValueError, base64.binascii.Error):
            return None, f"attachment[{i}] '{filename}': invalid base64"

        size = len(payload)

        if mime.startswith("image/"):
            if mime not in _ALLOWED_IMAGE_MIMES:
                return None, (
                    f"attachment[{i}] '{filename}': "
                    f"unsupported image type {mime}"
                )
            if size > _MAX_IMAGE_BYTES:
                return None, (
                    f"attachment[{i}] '{filename}': "
                    f"{size} bytes exceeds 5 MB image cap"
                )
        else:
            if size > _MAX_TEXT_BYTES:
                return None, (
                    f"attachment[{i}] '{filename}': "
                    f"{size} bytes exceeds 256 KB text cap"
                )
            try:
                payload.decode("utf-8")
            except UnicodeDecodeError:
                return None, (
                    f"attachment[{i}] '{filename}': "
                    f"not UTF-8 decodable"
                )

        total_bytes += size
        if total_bytes > _MAX_TOTAL_BYTES:
            return None, "total attachment size exceeds 20 MB cap"

        decoded.append({
            "filename": filename,
            "mime_type": mime,
            "bytes": payload,
        })

    return decoded, None


class WebSocketChannel:
    def __init__(self, in_queue: asyncio.Queue, host: str = "0.0.0.0", port: int = 8765, model: str = ""):
        self.in_queue = in_queue
        self.host = host
        self.port = port
        self.model = model
        self._connection: websockets.ServerConnection | None = None

    async def start(self) -> None:
        try:
            async with websockets.serve(self._handle_connection, self.host, self.port) as server:
                logger.info("WebSocket server listening on %s:%d", self.host, self.port)
                await asyncio.get_running_loop().create_future()
        except asyncio.CancelledError:
            logger.info("WebSocket server shutting down")
            if self._connection is not None:
                await self._connection.close()
            raise

    async def _handle_connection(self, websocket: websockets.ServerConnection) -> None:
        if self._connection is not None:
            old = self._connection
            logger.info("Replacing stale connection with new client")
            self._connection = None
            try:
                await old.close(1008, "Replaced by new connection")
            except Exception:
                pass

        self._connection = websocket
        remote = websocket.remote_address
        logger.info("Client connected from %s", remote)

        # Send welcome message with model info
        if self.model:
            welcome = json.dumps({"content": "", "model": self.model, "final": False})
            await websocket.send(welcome)

        try:
            async for raw in websocket:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Received invalid JSON from client, ignoring")
                    continue

                msg = IncomingMessage(
                    content=data.get("content", ""),
                    channel="cli",
                    session_id=SESSION_ID,
                    reply_address={},
                    command=data.get("command") or None,
                )
                await self.in_queue.put(msg)
        except websockets.exceptions.ConnectionClosedError:
            pass
        finally:
            if self._connection is websocket:
                self._connection = None
            logger.info("Client disconnected from %s", remote)
            extract_msg = IncomingMessage(
                content="",
                channel="cli",
                session_id=SESSION_ID,
                reply_address={},
                command="extract",
            )
            await self.in_queue.put(extract_msg)

    async def send(self, msg: OutgoingMessage) -> None:
        if self._connection is None:
            logger.warning("No WebSocket client connected; dropping outgoing message")
            return

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
            await self._connection.send(json.dumps(payload))
        except websockets.exceptions.ConnectionClosed:
            self._connection = None
            logger.warning("WebSocket connection closed while sending; message dropped")
