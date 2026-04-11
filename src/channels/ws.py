import asyncio
import json
import logging

import websockets
import websockets.exceptions

from src.channels.base import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)

SESSION_ID = "cli"


class WebSocketChannel:
    def __init__(self, in_queue: asyncio.Queue, host: str = "0.0.0.0", port: int = 8765,
                 model: str = "", local_mode: bool = False):
        self.in_queue = in_queue
        self.host = host
        self.port = port
        self.model = model
        self.local_mode = local_mode
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
            welcome = json.dumps({
                "content": "", "model": self.model, "final": False,
                "local_mode": self.local_mode,
            })
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
            "attachments": msg.attachments if msg.attachments else None,
            "workflow": msg.workflow,
            "stats": msg.stats,
        }
        try:
            await self._connection.send(json.dumps(payload))
        except websockets.exceptions.ConnectionClosed:
            self._connection = None
            logger.warning("WebSocket connection closed while sending; message dropped")
