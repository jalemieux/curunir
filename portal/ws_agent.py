"""Container-facing WebSocket endpoint.

Container dials wss://portal/ws/agent with `Authorization: Bearer <token>`.
Token is validated against `users.container_token`; valid → register on
the routing table; invalid/inactive → close 4003.

This endpoint reads messages from the container and routes by the
`session_id` riding on the payload, so traffic for one browser tab does
not bleed into another:
  - {"type": "agent_message", "payload": ...}        → unwrap, route the
    payload to browsers bound to `payload.session_id`.
  - {"type": "history_snapshot", "session_id": ...,
     "messages": ...}                                 → route the whole
    envelope to browsers bound to `session_id`.
  - {"type": "conversations_snapshot", "session_id": ...,
     "conversations": ...}                            → likewise, route
    the envelope to browsers bound to `session_id`.

If `session_id` is missing on either frame (stale container build), the
frame is dropped on the assumption that legacy single-session use is
already on its way out — fanning out without a session_id was the
source of the cross-tab bleed we are fixing.

Heartbeat contract. Between agent turns the socket carries no application
traffic, so Render's proxy hangs it up at its ~25–36s idle window and the
container flaps (issue #481). To hold the socket open the server drives an
application-level heartbeat: after accept it sends `{"type": "ping"}` every
`PORTAL_WS_HEARTBEAT_SEC` (env-overridable, default 15s — inside the idle
window with margin). The container answers `{"type": "pong"}`; both `ping`
and `pong` are silent no-ops here (no routing, no `unknown type` warning).
Data frames are used rather than protocol ping frames so the keepalive works
regardless of whether the proxy counts WS control frames as activity.
"""

import asyncio
import json
import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from portal import db
from portal.routing import routing
from portal.ws_common import bearer_from_headers


logger = logging.getLogger(__name__)
router = APIRouter()

# Interval between server→container heartbeat `ping` frames. Sits inside the
# observed ~25–36s proxy idle window with margin; env-overridable so the
# interval can be tightened without a code redeploy if the real timeout is
# lower.
PORTAL_WS_HEARTBEAT_SEC = float(os.environ.get("PORTAL_WS_HEARTBEAT_SEC", "15"))


async def _heartbeat(ws: WebSocket) -> None:
    """Send an application-level `ping` frame every heartbeat interval.

    Runs until cancelled (on disconnect) or the send fails because the socket
    closed. The interval is read from the module global on each tick so a
    test can shorten it via monkeypatch.
    """
    try:
        while True:
            await asyncio.sleep(PORTAL_WS_HEARTBEAT_SEC)
            await ws.send_text(json.dumps({"type": "ping"}))
    except (WebSocketDisconnect, RuntimeError):
        # Socket closed under us; the receive loop will unwind and clean up.
        pass


@router.websocket("/ws/agent")
async def ws_agent(ws: WebSocket) -> None:
    token = bearer_from_headers(ws)
    if not token:
        await ws.close(code=4003, reason="missing bearer token")
        return
    user = await db.get_active_user_by_container_token(token)
    if user is None:
        await ws.close(code=4003, reason="forbidden")
        return

    await ws.accept()
    await routing.register_agent(user.id, ws)
    logger.info("agent connected", extra={"user_id": user.id})

    heartbeat_task = asyncio.create_task(_heartbeat(ws))
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("agent sent invalid json", extra={"user_id": user.id})
                continue
            mtype = msg.get("type")
            if mtype in ("ping", "pong"):
                # Heartbeat frames (the container's `pong` reply, or a `ping`
                # it might originate) — silent no-op, not an agent turn.
                continue
            if mtype == "agent_message":
                payload = msg.get("payload") or {}
                session_id = payload.get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    logger.warning(
                        "agent_message without session_id; dropping",
                        extra={"user_id": user.id},
                    )
                    continue
                await routing.route_to_session(
                    user.id, session_id, json.dumps(payload)
                )
            elif mtype == "history_snapshot":
                session_id = msg.get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    logger.warning(
                        "history_snapshot without session_id; dropping",
                        extra={"user_id": user.id},
                    )
                    continue
                await routing.route_to_session(user.id, session_id, raw)
            elif mtype == "skills_snapshot":
                session_id = msg.get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    logger.warning(
                        "skills_snapshot without session_id; dropping",
                        extra={"user_id": user.id},
                    )
                    continue
                await routing.route_to_session(user.id, session_id, raw)
            elif mtype == "conversations_snapshot":
                session_id = msg.get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    logger.warning(
                        "conversations_snapshot without session_id; dropping",
                        extra={"user_id": user.id},
                    )
                    continue
                await routing.route_to_session(user.id, session_id, raw)
            else:
                logger.warning("agent sent unknown type %r", mtype,
                               extra={"user_id": user.id})
    except WebSocketDisconnect:
        pass
    finally:
        heartbeat_task.cancel()
        await routing.unregister_agent(user.id, ws)
        logger.info("agent disconnected", extra={"user_id": user.id})
