"""Browser-facing and native-client WebSocket endpoint.

Browser or native client opens wss://portal/ws/browser. Two auth paths:
  - Native client (e.g., iOS voice app): `Authorization: Bearer <client_token>`.
    The token is the sole credential; no Origin check (native apps don't send Origin).
  - Browser: session cookie + `Origin` header; both must be valid:
      - Cookie absent / invalid / user inactive → close 4003.
      - Origin header missing or != PORTAL_BASE_URL → close 4003 (CSWSH).

Both paths send `IncomingMessage`-shaped JSON. Each frame is expected to
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
from portal.ws_common import bearer_from_headers


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
    token = bearer_from_headers(ws)
    if token is not None:
        # Native client (e.g. the iOS voice app): the client_token is the
        # credential. No cookie, and no Origin check — native apps send no
        # Origin header, and CSWSH is a browser-only attack. An invalid
        # bearer never falls back to the cookie path.
        user = await db.get_active_user_by_client_token(token)
        if user is None:
            await ws.close(code=4003, reason="forbidden")
            return
    else:
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
                # Only surface a chat-bubble error for user-visible message
                # frames. Control frames (history_request, skills_request,
                # clear, interrupt) are bootstrap/housekeeping traffic — a
                # failed one must not leave a stale "Agent offline." bubble
                # in the transcript once the agent reconnects. The browser's
                # offline modal already communicates the disconnected state.
                command = payload.get("command")
                if command in (None, "slash"):
                    await ws.send_text(json.dumps({
                        "content": "Agent offline.",
                        "final": True,
                        "delta": False,
                        "session_id": session_id,
                    }))
                else:
                    # Bootstrap/housekeeping frame (history_request,
                    # skills_request, conversations_request, …) dropped because
                    # the agent hasn't dialed in yet — the cold-start race
                    # behind #334. The client recovers by re-issuing these on
                    # the agent's offline→online transition; log it so the
                    # condition is visible in portal logs.
                    logger.info(
                        "control frame dropped (agent offline)",
                        extra={"user_id": user.id, "command": command},
                    )
    except WebSocketDisconnect:
        pass
    finally:
        await routing.remove_browser(user.id, ws)
        logger.info("browser disconnected", extra={"user_id": user.id})
