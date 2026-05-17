"""In-memory user→connections routing table.

Single-process. Each user has at most one agent socket and zero or
more browser sockets. Each browser socket is associated with a
`session_id` (a per-tab UUID minted by the browser). Agent-emitted
frames carry that `session_id` and are routed only to the browser(s)
bound to it — open tabs running other sessions never see another
session's stream. The portal stores no chat content; this table is
the only stateful surface and is reset on portal restart.

Lifecycle:
- Browser connects → `add_browser` registers it as unbound.
- Browser's first frame (or any frame) carries `session_id` → the
  ws_browser handler calls `bind_browser_session` to associate it.
- A reload reuses the same `sessionStorage` id; a new tab mints a new
  one. The portal never persists ids — each ws starts unbound.
- `agent_status` is global (online/offline) so it stays a fan-out;
  per-session traffic uses `route_to_session`.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Protocol


logger = logging.getLogger(__name__)


class Sender(Protocol):
    """Anything we can json-send to and close. WebSocket-shaped."""
    async def send_text(self, data: str) -> None: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


@dataclass
class BrowserConn:
    ws: Sender
    session_id: str | None = None


@dataclass
class UserRoute:
    agent_ws: Sender | None = None
    browsers: list[BrowserConn] = field(default_factory=list)


class RoutingTable:
    def __init__(self) -> None:
        self._routes: dict[int, UserRoute] = {}
        self._lock = asyncio.Lock()

    def _route(self, user_id: int) -> UserRoute:
        return self._routes.setdefault(user_id, UserRoute())

    async def register_agent(self, user_id: int, ws: Sender) -> None:
        """Register agent ws for user. Kicks any prior agent (close 4002)."""
        async with self._lock:
            route = self._route(user_id)
            old = route.agent_ws
            route.agent_ws = ws
        if old is not None:
            try:
                await old.close(code=4002, reason="replaced")
            except Exception:
                logger.warning("error closing replaced agent ws", exc_info=True)
        await self._broadcast_status(user_id, "online")

    async def unregister_agent(self, user_id: int, ws: Sender) -> None:
        """Tear down an agent socket. Broadcasts offline only if `ws` was
        the *current* agent socket.

        A stale socket (one already superseded by a reconnect) can finish
        teardown long after the new socket registered — uvicorn may take up
        to its ws-ping timeout to notice an abruptly-dropped peer. In that
        case the route already points at the live socket; broadcasting
        offline would leave the browser stuck on the offline modal while the
        agent is actually connected.
        """
        cleared = False
        async with self._lock:
            route = self._routes.get(user_id)
            if route is not None and route.agent_ws is ws:
                route.agent_ws = None
                cleared = True
        if cleared:
            await self._broadcast_status(user_id, "offline")

    async def add_browser(self, user_id: int, ws: Sender) -> None:
        async with self._lock:
            self._route(user_id).browsers.append(BrowserConn(ws=ws))

    async def remove_browser(self, user_id: int, ws: Sender) -> None:
        async with self._lock:
            route = self._routes.get(user_id)
            if route is None:
                return
            route.browsers = [b for b in route.browsers if b.ws is not ws]

    async def bind_browser_session(
        self, user_id: int, ws: Sender, session_id: str
    ) -> None:
        """Associate a browser ws with a session_id. Idempotent.

        If the session_id changes mid-connection (unusual — would mean
        the tab cleared sessionStorage without reloading) the latest
        binding wins.
        """
        async with self._lock:
            route = self._routes.get(user_id)
            if route is None:
                return
            for b in route.browsers:
                if b.ws is ws:
                    b.session_id = session_id
                    return

    def agent_for(self, user_id: int) -> Sender | None:
        route = self._routes.get(user_id)
        return route.agent_ws if route else None

    def online_agent_user_ids(self) -> set[int]:
        """User ids that currently have a connected agent socket."""
        return {
            uid for uid, route in self._routes.items()
            if route.agent_ws is not None
        }

    def browsers_for(self, user_id: int) -> list[Sender]:
        """All browser sockets for the user (regardless of session binding)."""
        route = self._routes.get(user_id)
        return [b.ws for b in route.browsers] if route else []

    def browsers_for_session(
        self, user_id: int, session_id: str
    ) -> list[Sender]:
        """Browser sockets bound to the given session_id."""
        route = self._routes.get(user_id)
        if route is None:
            return []
        return [b.ws for b in route.browsers if b.session_id == session_id]

    async def fan_out_to_browsers(self, user_id: int, payload: str) -> int:
        """Send payload to all browsers; return count delivered.

        Reserved for global frames (agent_status). Per-session traffic
        should use `route_to_session`.
        """
        return await self._send_to(self.browsers_for(user_id), payload, user_id)

    async def route_to_session(
        self, user_id: int, session_id: str, payload: str
    ) -> int:
        """Send payload only to browsers bound to `session_id`."""
        targets = self.browsers_for_session(user_id, session_id)
        if not targets:
            logger.info(
                "agent_message dropped (no browser bound to session)",
                extra={"user_id": user_id, "session_id": session_id},
            )
            return 0
        return await self._send_to(targets, payload, user_id)

    async def _send_to(
        self, targets: list[Sender], payload: str, user_id: int
    ) -> int:
        if not targets:
            logger.info("agent_message dropped (no browsers)", extra={"user_id": user_id})
            return 0
        delivered = 0
        for ws in targets:
            try:
                await ws.send_text(payload)
                delivered += 1
            except Exception:
                logger.warning("browser send failed", exc_info=True)
        return delivered

    async def forward_to_agent(self, user_id: int, payload: str) -> bool:
        agent = self.agent_for(user_id)
        if agent is None:
            return False
        try:
            await agent.send_text(payload)
            return True
        except Exception:
            logger.warning("agent send failed", exc_info=True)
            return False

    async def _broadcast_status(self, user_id: int, status: str) -> None:
        import json
        payload = json.dumps({"type": "agent_status", "status": status})
        await self.fan_out_to_browsers(user_id, payload)


routing = RoutingTable()
