"""PortalChannel — outbound WebSocket to the Curunir Portal.

Connects to wss://<portal>/ws/agent with `Authorization: Bearer <token>`.
Receives wrapped user messages from the portal; emits wrapped agent
messages and history snapshots back. Reconnects with exponential
backoff on retryable failures. Auth failure (4003) and replaced
(4002) are terminal.

Enabled when both CURUNIR_PORTAL_URL and CURUNIR_PORTAL_TOKEN are set.
"""

import asyncio
import json
import logging
import os
import random
from typing import Any

import websockets
import websockets.exceptions

from src.channels._attachments import (
    _decode_attachments,
    _enrich_attachments,
    _stage_attachments,
)
from src.channels.base import IncomingMessage, OutgoingMessage


logger = logging.getLogger(__name__)

PORTAL_SESSION_ID = "portal"  # Legacy fallback when the portal omits session_id.

_BACKOFF_INITIAL = 1.0
_BACKOFF_MAX = 30.0
_TERMINAL_CODES = {4002, 4003}


def _backoff_with_jitter(attempt: int) -> float:
    base = min(_BACKOFF_INITIAL * (2 ** attempt), _BACKOFF_MAX)
    jitter = base * 0.2 * (random.random() * 2 - 1)
    return max(0.0, base + jitter)


class PortalChannel:
    def __init__(
        self,
        in_queue: asyncio.Queue,
        url: str,
        token: str,
        history_provider: "callable[[str], list[dict]] | None" = None,
        uploads_dir: str | None = None,
        cancel_session: "callable[[str], bool] | None" = None,
    ):
        self.in_queue = in_queue
        self.url = url
        self.token = token
        self.history_provider = history_provider or (lambda _sid: [])
        self.uploads_dir = uploads_dir or os.path.join(
            os.getcwd(), "context", "uploads"
        )
        self.cancel_session = cancel_session
        self._connection: Any = None
        self._terminate = False

    async def start(self) -> None:
        attempt = 0
        while not self._terminate:
            try:
                async with websockets.connect(
                    self.url,
                    additional_headers={"Authorization": f"Bearer {self.token}"},
                    max_size=32 * 1024 * 1024,
                ) as ws:
                    logger.info("PortalChannel connected to %s", self.url)
                    self._connection = ws
                    attempt = 0
                    await self._read_loop(ws)
            except websockets.exceptions.InvalidStatus as e:
                code = getattr(e.response, "status_code", None)
                logger.error("Portal upgrade rejected: %s", code)
                if code in (401, 403):
                    self._terminate = True
                    return
            except websockets.exceptions.ConnectionClosed as e:
                code = e.code
                if code in _TERMINAL_CODES:
                    logger.error("Portal closed connection (terminal): %s %s",
                                 code, e.reason)
                    self._terminate = True
                    return
                logger.warning("Portal connection closed: %s %s; will retry",
                               code, e.reason)
            except (OSError, asyncio.TimeoutError) as e:
                logger.warning("Portal connect failed: %s; will retry", e)
            except asyncio.CancelledError:
                logger.info("PortalChannel cancelled")
                raise
            finally:
                self._connection = None

            if self._terminate:
                return
            delay = _backoff_with_jitter(attempt)
            attempt = min(attempt + 1, 6)
            await asyncio.sleep(delay)

    async def _read_loop(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Portal sent invalid JSON; ignoring")
                continue
            mtype = msg.get("type")
            if mtype == "user_message":
                await self._handle_user_message(msg.get("payload") or {})
            elif mtype == "history_request":
                await self._handle_history_request(msg.get("payload") or {})
            else:
                logger.warning("Portal sent unknown type %r; ignoring", mtype)

    async def _handle_user_message(self, payload: dict) -> None:
        session_id = payload.get("session_id") or PORTAL_SESSION_ID
        if payload.get("command") == "interrupt":
            delivered = bool(self.cancel_session and self.cancel_session(PORTAL_SESSION_ID))
            logger.info("Interrupt requested for portal session (delivered=%s)", delivered)
            return

        decoded, err = _decode_attachments(payload.get("attachments"))
        if err is not None:
            await self.send(OutgoingMessage(
                content=f"Attachment rejected: {err}",
                channel="portal",
                session_id=session_id,
                reply_address={},
                final=True,
            ))
            return

        manifest = (
            _stage_attachments(decoded, session_id, self.uploads_dir)
            if decoded else None
        )
        await self.in_queue.put(IncomingMessage(
            content=payload.get("content", ""),
            channel="portal",
            session_id=session_id,
            reply_address={},
            command=payload.get("command") or None,
            attachments=manifest,
        ))

    async def _handle_history_request(self, payload: dict) -> None:
        if self._connection is None:
            return
        session_id = payload.get("session_id") or PORTAL_SESSION_ID
        messages = self.history_provider(session_id)
        try:
            await self._connection.send(json.dumps({
                "type": "history_snapshot",
                "messages": messages,
            }))
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Portal closed during history snapshot send")

    async def send(self, msg: OutgoingMessage) -> None:
        if self._connection is None:
            logger.info("PortalChannel: no portal connection; dropping outbound")
            return
        if msg.attachments:
            _enrich_attachments(msg.attachments, os.getcwd())

        wrapped = {
            "type": "agent_message",
            "payload": {
                "session_id": msg.session_id,
                "content": msg.content,
                "tool_calls": msg.tool_calls,
                "final": msg.final,
                "delta": msg.delta,
                "attachments": msg.attachments if msg.attachments else None,
                "workflow": msg.workflow,
                "stats": msg.stats,
            },
        }
        try:
            await self._connection.send(json.dumps(wrapped))
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Portal connection closed while sending; dropped")
            self._connection = None
