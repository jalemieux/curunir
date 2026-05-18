"""PortalChannel — outbound WebSocket to the Curunir Portal.

Connects to wss://<portal>/ws/agent with `Authorization: Bearer <token>`.
Receives wrapped user messages from the portal; emits wrapped agent
messages and history snapshots back. Reconnects with exponential
backoff on retryable failures. Auth failure (4003) and replaced
(4002) are terminal.

Outbound agent_message frames sent while disconnected (or that fail
mid-send) are buffered and replayed in order on the next successful
connect, so a brief socket blip doesn't silently swallow a reply.
History snapshots and streaming deltas are not buffered — both are
regenerable.

Inbound chat-content `user_message` frames are deduplicated within a
short time window: the portal/network layer sometimes delivers the
same frame 2–3 times, and each duplicate would otherwise run a full
agent turn. An identical frame for the same session seen within
`_DEDUP_WINDOW_SEC` is dropped. Control frames (interrupt, history,
skills) are not deduped.

Enabled when both CURUNIR_PORTAL_URL and CURUNIR_PORTAL_TOKEN are set.
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import time
from collections import deque
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

# How many outbound frames to hold while disconnected. A full agent reply
# (text + attachments) is one frame, so this is generous; the oldest frames
# are dropped first if it overflows.
_OUTBOUND_BUFFER_MAX = 64

# Inbound dedup: an identical content frame for the same session seen
# within this window is treated as a duplicate and dropped. The observed
# duplicates land within ~0–2s of each other; a 5s window stays well clear
# of a user legitimately retyping the same message minutes later.
_DEDUP_WINDOW_SEC = 5.0
# How many recent content-frame hashes to retain for the dedup check.
_DEDUP_HISTORY_MAX = 32


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
        skills_provider: "callable[[], list[dict]] | None" = None,
        uploads_dir: str | None = None,
        cancel_session: "callable[[str], bool] | None" = None,
    ):
        self.in_queue = in_queue
        self.url = url
        self.token = token
        self.history_provider = history_provider or (lambda _sid: [])
        self.skills_provider = skills_provider or (lambda: [])
        self.uploads_dir = uploads_dir or os.path.join(
            os.getcwd(), "context", "uploads"
        )
        self.cancel_session = cancel_session
        self._connection: Any = None
        self._terminate = False
        # Serialized agent_message frames that couldn't be sent because the
        # socket was down; flushed (in order) on the next successful connect.
        self._outbound: deque[str] = deque(maxlen=_OUTBOUND_BUFFER_MAX)
        # (monotonic_ts, frame_hash) of recently-seen content frames, for
        # time-windowed inbound dedup. See _is_duplicate.
        self._recent: deque[tuple[float, str]] = deque(maxlen=_DEDUP_HISTORY_MAX)

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
                    attempt = 0
                    # Drain anything buffered while disconnected before we
                    # publish the connection — keeps _connection None so any
                    # concurrent send() appends behind the backlog rather than
                    # racing ahead of it.
                    await self._flush_outbound(ws)
                    self._connection = ws
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
            elif mtype == "skills_request":
                await self._handle_skills_request(msg.get("payload") or {})
            else:
                logger.warning("Portal sent unknown type %r; ignoring", mtype)

    async def _handle_user_message(self, payload: dict) -> None:
        session_id = payload.get("session_id") or PORTAL_SESSION_ID
        if payload.get("command") == "interrupt":
            delivered = bool(self.cancel_session and self.cancel_session(session_id))
            logger.info(
                "Interrupt requested for portal session %s (delivered=%s)",
                session_id, delivered,
            )
            return
        if payload.get("command") == "history_request":
            await self._handle_history_request({"session_id": session_id})
            return

        if payload.get("command") == "skills_request":
            await self._handle_skills_request({"session_id": session_id})
            return

        if payload.get("command") == "slash":
            # Channels don't interpret slash — agent_worker does.
            # Forward the raw text on the queue with command=slash.
            await self.in_queue.put(IncomingMessage(
                content=payload.get("text", ""),
                channel="portal",
                session_id=session_id,
                reply_address={},
                command="slash",
            ))
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

        if self._is_duplicate(payload):
            logger.info(
                "PortalChannel: dropped duplicate user_message (session %s)",
                session_id,
            )
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

    def _is_duplicate(self, payload: dict) -> bool:
        """True if this content frame was already seen within the dedup window.

        The portal/network layer occasionally delivers the same `user_message`
        frame 2–3 times; without this check each copy runs a full agent turn.
        The payload carries `session_id`, `content`, and `attachments`, so an
        identical resend hashes identically. Entries older than the window are
        pruned so a legitimate later resend of the same text is not dropped.
        """
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        now = time.monotonic()
        while self._recent and now - self._recent[0][0] > _DEDUP_WINDOW_SEC:
            self._recent.popleft()
        if any(h == digest for _, h in self._recent):
            return True
        self._recent.append((now, digest))
        return False

    async def _handle_history_request(self, payload: dict) -> None:
        if self._connection is None:
            return
        session_id = payload.get("session_id") or PORTAL_SESSION_ID
        messages = self.history_provider(session_id)
        try:
            await self._connection.send(json.dumps({
                "type": "history_snapshot",
                "session_id": session_id,
                "messages": messages,
            }))
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Portal closed during history snapshot send")

    async def _handle_skills_request(self, payload: dict) -> None:
        if self._connection is None:
            return
        session_id = payload.get("session_id") or PORTAL_SESSION_ID
        skills = self.skills_provider()
        try:
            await self._connection.send(json.dumps({
                "type": "skills_snapshot",
                "session_id": session_id,
                "skills": skills,
            }))
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Portal closed during skills snapshot send")

    async def _flush_outbound(self, ws) -> None:
        """Replay buffered frames over a freshly-opened socket, in order.

        Pops each frame only after it's sent, so a mid-flush disconnect
        leaves the remainder buffered for the next reconnect. Concurrent
        send() calls (which see _connection still None) append behind us.
        """
        if self._outbound:
            logger.info("PortalChannel: flushing %d buffered outbound frame(s)",
                        len(self._outbound))
        while self._outbound:
            await ws.send(self._outbound[0])
            self._outbound.popleft()

    async def send(self, msg: OutgoingMessage) -> None:
        # Enrich first so a buffered frame is fully self-contained (file
        # content inlined, paths normalized) — don't want to re-read disk
        # on flush, the file may be gone by then.
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
        frame = json.dumps(wrapped)

        if self._connection is None:
            if msg.delta:
                # Streaming deltas are ephemeral UI updates; the final
                # message carries the full content, so drop them silently.
                return
            self._outbound.append(frame)
            logger.info("PortalChannel: no connection; buffered outbound "
                        "(%d queued)", len(self._outbound))
            return
        try:
            await self._connection.send(frame)
        except websockets.exceptions.ConnectionClosed:
            self._connection = None
            if msg.delta:
                logger.warning("Portal closed while sending delta; dropped")
                return
            self._outbound.append(frame)
            logger.warning("Portal closed while sending; buffered outbound "
                           "(%d queued)", len(self._outbound))
