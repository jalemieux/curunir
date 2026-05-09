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
async def test_route_to_session_only_targets_bound_browsers():
    rt = RoutingTable()
    b1, b2, b3 = FakeWS(), FakeWS(), FakeWS()
    await rt.add_browser(5, b1)
    await rt.add_browser(5, b2)
    await rt.add_browser(5, b3)
    await rt.bind_browser_session(5, b1, "tab-A")
    await rt.bind_browser_session(5, b2, "tab-B")
    # b3 stays unbound

    delivered = await rt.route_to_session(5, "tab-A", "for-A")
    assert delivered == 1
    assert b1.sent[-1] == "for-A"
    # The other tabs must not receive A's stream — that was the cross-tab bleed bug.
    assert "for-A" not in b2.sent
    assert "for-A" not in b3.sent


@pytest.mark.asyncio
async def test_route_to_session_returns_zero_when_no_binding():
    rt = RoutingTable()
    b = FakeWS()
    await rt.add_browser(6, b)
    # Browser registered but never bound.
    delivered = await rt.route_to_session(6, "tab-A", "payload")
    assert delivered == 0
    assert b.sent == []


@pytest.mark.asyncio
async def test_bind_browser_session_is_idempotent_and_overwrites():
    rt = RoutingTable()
    b = FakeWS()
    await rt.add_browser(7, b)
    await rt.bind_browser_session(7, b, "tab-A")
    await rt.bind_browser_session(7, b, "tab-A")  # idempotent
    await rt.bind_browser_session(7, b, "tab-B")  # rebinds
    await rt.route_to_session(7, "tab-A", "old")
    await rt.route_to_session(7, "tab-B", "new")
    assert "old" not in b.sent
    assert "new" in b.sent


@pytest.mark.asyncio
async def test_status_broadcast_reaches_unbound_browsers():
    """agent_status is global — even tabs that haven't sent a frame yet
    should learn the agent is online/offline."""
    rt = RoutingTable()
    unbound = FakeWS()
    bound = FakeWS()
    await rt.add_browser(8, unbound)
    await rt.add_browser(8, bound)
    await rt.bind_browser_session(8, bound, "tab-X")

    agent = FakeWS()
    await rt.register_agent(8, agent)

    statuses_unbound = [json.loads(s) for s in unbound.sent]
    statuses_bound = [json.loads(s) for s in bound.sent]
    assert {"type": "agent_status", "status": "online"} in statuses_unbound
    assert {"type": "agent_status", "status": "online"} in statuses_bound


@pytest.mark.asyncio
async def test_remove_browser_drops_binding():
    rt = RoutingTable()
    b = FakeWS()
    await rt.add_browser(9, b)
    await rt.bind_browser_session(9, b, "tab-A")
    await rt.remove_browser(9, b)
    delivered = await rt.route_to_session(9, "tab-A", "payload")
    assert delivered == 0


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
