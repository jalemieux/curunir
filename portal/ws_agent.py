"""Container-facing WebSocket endpoint.

Container dials wss://portal/ws/agent with `Authorization: Bearer <token>`.
Token is validated against `users.container_token`; valid → register on
the routing table; invalid/inactive → close 4003.

This endpoint reads messages from the container and either:
  - {"type": "agent_message", "payload": ...} → unwrap and fan out
    payload (alone) to the user's browsers
  - {"type": "history_snapshot", "messages": ...} → fan out the full
    envelope to browsers (browser side knows how to render snapshots)

Browser-bound payloads are the unwrapped OutgoingMessage envelope.
"""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from portal import db
from portal.routing import routing


logger = logging.getLogger(__name__)
router = APIRouter()


def _bearer_from_headers(ws: WebSocket) -> str | None:
    auth = ws.headers.get("authorization") or ws.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


@router.websocket("/ws/agent")
async def ws_agent(ws: WebSocket) -> None:
    token = _bearer_from_headers(ws)
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

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("agent sent invalid json", extra={"user_id": user.id})
                continue
            mtype = msg.get("type")
            if mtype == "agent_message":
                payload = msg.get("payload") or {}
                await routing.fan_out_to_browsers(user.id, json.dumps(payload))
            elif mtype == "history_snapshot":
                await routing.fan_out_to_browsers(user.id, raw)
            else:
                logger.warning("agent sent unknown type %r", mtype,
                               extra={"user_id": user.id})
    except WebSocketDisconnect:
        pass
    finally:
        await routing.unregister_agent(user.id, ws)
        logger.info("agent disconnected", extra={"user_id": user.id})
