"""Browser-facing WebSocket endpoint.

Browser opens wss://portal/ws/browser; the session cookie rides on the
upgrade request. Cookie + `Origin` header must both be valid:
  - Cookie absent / invalid / user inactive → close 4003.
  - Origin header missing or != PORTAL_BASE_URL → close 4003 (CSWSH).

Browser sends `IncomingMessage`-shaped JSON. Each frame is expected to
carry a `session_id` (per-tab UUID). The first frame's `session_id` is
recorded against this socket so that agent-emitted traffic for that
session can be routed back here without bleeding into other tabs.

If a frame omits `session_id` (stale browser build) the socket stays
unbound and only receives global frames such as `agent_status` —
single-tab use still works because the browser's later frames will
typically include the id once it reloads against the new build.
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

    # Push current agent status. History is bootstrapped by the browser
    # itself once it has a session_id (it sends a history_request frame
    # that we forward to the agent).
    await ws.send_text(json.dumps({
        "type": "agent_status",
        "status": "online" if routing.agent_for(user.id) else "offline",
    }))

    bound_session: str | None = None
    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("browser sent invalid json",
                               extra={"user_id": user.id})
                continue

            session_id = payload.get("session_id")
            if isinstance(session_id, str) and session_id and session_id != bound_session:
                await routing.bind_browser_session(user.id, ws, session_id)
                bound_session = session_id

            wrapped = json.dumps({"type": "user_message", "payload": payload})
            ok = await routing.forward_to_agent(user.id, wrapped)
            if not ok:
                await ws.send_text(json.dumps({
                    "content": "Agent offline.",
                    "final": True,
                    "delta": False,
                    "session_id": session_id,
                }))
    except WebSocketDisconnect:
        pass
    finally:
        await routing.remove_browser(user.id, ws)
        logger.info("browser disconnected", extra={"user_id": user.id})
