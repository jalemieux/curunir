import json

import pytest

from portal.routing import RoutingTable


class FakeWS:
    def __init__(self):
        self.sent: list[str] = []
        self.closed_with: tuple[int, str] | None = None

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)


@pytest.mark.asyncio
async def test_register_agent_kicks_prior():
    rt = RoutingTable()
    a, b = FakeWS(), FakeWS()
    await rt.register_agent(7, a)
    await rt.register_agent(7, b)
    assert a.closed_with == (4002, "replaced")
    assert rt.agent_for(7) is b


@pytest.mark.asyncio
async def test_register_agent_broadcasts_online_to_browsers():
    rt = RoutingTable()
    browser = FakeWS()
    await rt.add_browser(9, browser)
    agent = FakeWS()
    await rt.register_agent(9, agent)
    statuses = [json.loads(s) for s in browser.sent]
    assert any(s == {"type": "agent_status", "status": "online"} for s in statuses)


@pytest.mark.asyncio
async def test_unregister_agent_broadcasts_offline():
    rt = RoutingTable()
    agent = FakeWS()
    browser = FakeWS()
    await rt.register_agent(1, agent)
    await rt.add_browser(1, browser)
    browser.sent.clear()
    await rt.unregister_agent(1, agent)
    statuses = [json.loads(s) for s in browser.sent]
    assert {"type": "agent_status", "status": "offline"} in statuses


@pytest.mark.asyncio
async def test_fan_out_to_browsers_delivers_to_all():
    rt = RoutingTable()
    b1, b2 = FakeWS(), FakeWS()
    await rt.add_browser(3, b1)
    await rt.add_browser(3, b2)
    delivered = await rt.fan_out_to_browsers(3, "hello")
    assert delivered == 2
    assert b1.sent[-1] == "hello"
    assert b2.sent[-1] == "hello"


@pytest.mark.asyncio
async def test_forward_to_agent_returns_false_when_no_agent():
    rt = RoutingTable()
    assert await rt.forward_to_agent(11, "msg") is False


@pytest.mark.asyncio
async def test_unregister_agent_only_clears_if_same_socket():
    rt = RoutingTable()
    a, b = FakeWS(), FakeWS()
    await rt.register_agent(2, a)
    await rt.register_agent(2, b)  # kicks a
    await rt.unregister_agent(2, a)  # stale unregister of a
    assert rt.agent_for(2) is b
