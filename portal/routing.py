"""In-memory user→connections routing table.

Single-process. Each user has at most one agent socket and zero or
more browser sockets. The portal stores no chat content; this table
is the only stateful surface and is reset on portal restart.
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
class UserRoute:
    agent_ws: Sender | None = None
    browser_wss: list[Sender] = field(default_factory=list)


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
        async with self._lock:
            route = self._routes.get(user_id)
            if route is not None and route.agent_ws is ws:
                route.agent_ws = None
        await self._broadcast_status(user_id, "offline")

    async def add_browser(self, user_id: int, ws: Sender) -> None:
        async with self._lock:
            self._route(user_id).browser_wss.append(ws)

    async def remove_browser(self, user_id: int, ws: Sender) -> None:
        async with self._lock:
            route = self._routes.get(user_id)
            if route is not None and ws in route.browser_wss:
                route.browser_wss.remove(ws)

    def agent_for(self, user_id: int) -> Sender | None:
        route = self._routes.get(user_id)
        return route.agent_ws if route else None

    def browsers_for(self, user_id: int) -> list[Sender]:
        route = self._routes.get(user_id)
        return list(route.browser_wss) if route else []

    def all_agents(self) -> list[Sender]:
        """Snapshot of every currently registered agent socket."""
        return [r.agent_ws for r in self._routes.values() if r.agent_ws is not None]

    async def fan_out_to_browsers(self, user_id: int, payload: str) -> int:
        """Send payload to all browsers; return count delivered."""
        targets = self.browsers_for(user_id)
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
