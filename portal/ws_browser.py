"""Browser-facing WebSocket endpoint.

Browser opens wss://portal/ws/browser; the session cookie rides on the
upgrade request. Cookie + `Origin` header must both be valid:
  - Cookie absent / invalid / user inactive → close 4003.
  - Origin header missing or != PORTAL_BASE_URL → close 4003 (CSWSH).

Browser sends `IncomingMessage`-shaped JSON. We wrap as
`{"type": "user_message", "payload": ...}` and forward to the user's
agent socket. If no agent is connected, we reply directly to *this*
browser with a synthetic offline message — other browsers do not see it.
"""

import json
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from portal import auth, db
from portal.config import settings
from portal.routing import routing


logger = logging.getLogger(__name__)
router = APIRouter()


def _origin_allowed(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:
        return False
    expected = urlparse(settings.portal_base_url)
    actual = urlparse(origin)
    return (expected.scheme, expected.netloc) == (actual.scheme, actual.netloc)


@router.websocket("/ws/browser")
async def ws_browser(ws: WebSocket) -> None:
    if not _origin_allowed(ws):
        await ws.close(code=4003, reason="origin")
        return

    cookie = ws.cookies.get(auth.SESSION_COOKIE)
    if not cookie:
        await ws.close(code=4003, reason="no cookie")
        return
    user_id = auth.verify_session(cookie)
    if user_id is None:
        await ws.close(code=4003, reason="bad cookie")
        return
    user = await db.get_user_by_id(user_id)
    if user is None or not user.is_active:
        await ws.close(code=4003, reason="inactive")
        return

    await ws.accept()
    await routing.add_browser(user.id, ws)
    logger.info("browser connected", extra={"user_id": user.id})

    # Initial state push: agent_status + history_request to the agent
    # (agent will respond with history_snapshot which fans out to browsers).
    await ws.send_text(json.dumps({
        "type": "agent_status",
        "status": "online" if routing.agent_for(user.id) else "offline",
    }))
    if routing.agent_for(user.id) is not None:
        await routing.forward_to_agent(
            user.id, json.dumps({"type": "history_request"})
        )

    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("browser sent invalid json",
                               extra={"user_id": user.id})
                continue
            wrapped = json.dumps({"type": "user_message", "payload": payload})
            ok = await routing.forward_to_agent(user.id, wrapped)
            if not ok:
                await ws.send_text(json.dumps({
                    "content": "Agent offline.",
                    "final": True,
                    "delta": False,
                }))
    except WebSocketDisconnect:
        pass
    finally:
        await routing.remove_browser(user.id, ws)
        logger.info("browser disconnected", extra={"user_id": user.id})
