"""Tests for the lifespan-shutdown broadcast to agents.

Hard-to-test path via TestClient lifespans, so we exercise the helper
directly with fake agent sockets registered on the routing table.
"""

import json

import pytest

from portal import app as portal_app
from portal.routing import RoutingTable, routing


class FakeAgent:
    def __init__(self):
        self.sent: list[str] = []
        self.closed_with: tuple[int, str] | None = None

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)


@pytest.fixture
def clean_routing():
    routing._routes.clear()
    yield
    routing._routes.clear()


@pytest.mark.asyncio
async def test_shutdown_agents_sends_shutdown_and_closes(clean_routing):
    a1, a2 = FakeAgent(), FakeAgent()
    await routing.register_agent(1, a1)
    await routing.register_agent(2, a2)

    await portal_app._shutdown_agents()

    for agent in (a1, a2):
        assert any(
            json.loads(s) == {"type": "shutdown"} for s in agent.sent
        ), f"agent never received shutdown: {agent.sent}"
        assert agent.closed_with is not None
        code, _ = agent.closed_with
        assert code == 1012


@pytest.mark.asyncio
async def test_shutdown_agents_tolerates_per_agent_failure(clean_routing):
    """A single agent's send/close failing must not skip the others."""

    class FailingAgent(FakeAgent):
        async def send_text(self, data: str) -> None:
            raise RuntimeError("socket dead")

    bad = FailingAgent()
    good = FakeAgent()
    await routing.register_agent(10, bad)
    await routing.register_agent(11, good)

    await portal_app._shutdown_agents()

    assert any(
        json.loads(s) == {"type": "shutdown"} for s in good.sent
    )
    assert good.closed_with is not None
