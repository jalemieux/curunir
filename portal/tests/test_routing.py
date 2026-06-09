import json

import pytest

from portal.routing import MAX_BUFFERED_FRAMES, RoutingTable


class FakeWS:
    def __init__(self):
        self.sent: list[str] = []
        self.closed_with: tuple[int, str] | None = None

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)


class DeadWS:
    """A browser socket that has gone away but isn't yet unregistered —
    every send raises, as a real closed WebSocket would."""

    def __init__(self):
        self.sent: list[str] = []
        self.closed_with: tuple[int, str] | None = None

    async def send_text(self, data: str) -> None:
        raise ConnectionError("socket closed")

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


@pytest.mark.asyncio
async def test_stale_unregister_does_not_broadcast_offline():
    """A superseded agent socket tearing down (e.g. the old socket after a
    1006 reconnect) must not broadcast offline — the newer socket is live and
    already announced online. Broadcasting offline here leaves the browser
    stuck on the offline modal while the agent is actually connected."""
    rt = RoutingTable()
    browser = FakeWS()
    await rt.add_browser(1, browser)
    a, b = FakeWS(), FakeWS()
    await rt.register_agent(1, a)   # online
    await rt.register_agent(1, b)   # kicks a; agent still online via b
    browser.sent.clear()
    await rt.unregister_agent(1, a)  # stale teardown of the old socket
    statuses = [json.loads(s) for s in browser.sent]
    assert {"type": "agent_status", "status": "offline"} not in statuses


@pytest.mark.asyncio
async def test_bootstrap_request_dropped_before_agent_then_status_online_on_connect():
    """The ordering invariant the client-side #334 fix keys off.

    On a cold start the browser connects before the agent container has
    dialed in. The browser's bootstrap frames (history/skills/conversations)
    are forwarded while no agent socket exists — forward_to_agent returns
    False and the frame is silently dropped (no chat-bubble, no exception).
    Moments later the agent registers and the *only* signal the browser gets
    is a single agent_status:online broadcast. The client uses that rising
    edge to re-issue the dropped bootstrap requests.
    """
    rt = RoutingTable()
    browser = FakeWS()
    await rt.add_browser(42, browser)

    # Bootstrap request arrives while the agent is offline → dropped, no raise.
    forwarded = await rt.forward_to_agent(
        42, json.dumps({"command": "conversations_request"})
    )
    assert forwarded is False
    # No chat-bubble or other frame is produced as a side effect of the drop.
    assert browser.sent == []

    # Agent dials in: the browser's sole signal is one agent_status:online.
    agent = FakeWS()
    await rt.register_agent(42, agent)
    statuses = [json.loads(s) for s in browser.sent]
    assert statuses == [{"type": "agent_status", "status": "online"}]


@pytest.mark.asyncio
async def test_online_agent_user_ids_reports_connected_agents():
    rt = RoutingTable()
    await rt.register_agent(1, FakeWS())
    await rt.register_agent(2, FakeWS())
    await rt.add_browser(3, FakeWS())  # browser only, no agent
    assert rt.online_agent_user_ids() == {1, 2}


@pytest.mark.asyncio
async def test_online_agent_user_ids_excludes_disconnected_agent():
    rt = RoutingTable()
    agent = FakeWS()
    await rt.register_agent(5, agent)
    await rt.unregister_agent(5, agent)
    assert rt.online_agent_user_ids() == set()


# --- Session buffering: frames for an unbound session are held and replayed
# when a browser binds, instead of being dropped (the reconnect-loses-binding
# bug where a reply arrives while the browser ws is mid-reconnect). ---


@pytest.mark.asyncio
async def test_route_to_session_buffers_when_no_browser_bound():
    rt = RoutingTable()
    delivered = await rt.route_to_session(5, "tab-A", "payload-1")
    assert delivered == 0
    # A browser connects and binds to that session — it receives the frame
    # that arrived while it was away.
    b = FakeWS()
    await rt.add_browser(5, b)
    await rt.bind_browser_session(5, b, "tab-A")
    assert b.sent == ["payload-1"]


@pytest.mark.asyncio
async def test_buffered_frames_flush_in_arrival_order():
    rt = RoutingTable()
    await rt.route_to_session(1, "tab-A", "first")
    await rt.route_to_session(1, "tab-A", "second")
    b = FakeWS()
    await rt.add_browser(1, b)
    await rt.bind_browser_session(1, b, "tab-A")
    assert b.sent == ["first", "second"]


@pytest.mark.asyncio
async def test_buffer_is_per_session():
    rt = RoutingTable()
    await rt.route_to_session(1, "tab-A", "for-A")
    await rt.route_to_session(1, "tab-B", "for-B")
    b = FakeWS()
    await rt.add_browser(1, b)
    await rt.bind_browser_session(1, b, "tab-A")
    assert b.sent == ["for-A"]


@pytest.mark.asyncio
async def test_buffer_cleared_after_flush():
    """A second browser binding to the same session must not re-receive
    frames the first browser already drained."""
    rt = RoutingTable()
    await rt.route_to_session(1, "tab-A", "once")
    b1 = FakeWS()
    await rt.add_browser(1, b1)
    await rt.bind_browser_session(1, b1, "tab-A")
    assert b1.sent == ["once"]
    b2 = FakeWS()
    await rt.add_browser(1, b2)
    await rt.bind_browser_session(1, b2, "tab-A")
    assert b2.sent == []


@pytest.mark.asyncio
async def test_live_delivery_does_not_buffer():
    """When a browser is already bound, frames go live and are not also
    buffered — a later bind by another browser sees nothing."""
    rt = RoutingTable()
    b1 = FakeWS()
    await rt.add_browser(1, b1)
    await rt.bind_browser_session(1, b1, "tab-A")
    await rt.route_to_session(1, "tab-A", "live")
    assert b1.sent == ["live"]
    b2 = FakeWS()
    await rt.add_browser(1, b2)
    await rt.bind_browser_session(1, b2, "tab-A")
    assert b2.sent == []


@pytest.mark.asyncio
async def test_session_buffer_is_capped_keeping_newest():
    rt = RoutingTable()
    overflow = MAX_BUFFERED_FRAMES + 10
    for i in range(overflow):
        await rt.route_to_session(1, "tab-A", f"f{i}")
    b = FakeWS()
    await rt.add_browser(1, b)
    await rt.bind_browser_session(1, b, "tab-A")
    assert len(b.sent) == MAX_BUFFERED_FRAMES
    assert b.sent[-1] == f"f{overflow - 1}"
    assert "f0" not in b.sent


@pytest.mark.asyncio
async def test_route_to_session_buffers_when_bound_browser_send_fails():
    """A browser bound to the session but whose socket is dead (mid-disconnect,
    not yet unregistered) must not eat the frame: with no live delivery, the
    frame is buffered and replayed when a live browser binds."""
    rt = RoutingTable()
    dead = DeadWS()
    await rt.add_browser(1, dead)
    await rt.bind_browser_session(1, dead, "tab-A")

    delivered = await rt.route_to_session(1, "tab-A", "payload-1")
    assert delivered == 0

    live = FakeWS()
    await rt.add_browser(1, live)
    await rt.bind_browser_session(1, live, "tab-A")
    assert live.sent == ["payload-1"]


@pytest.mark.asyncio
async def test_partial_live_delivery_does_not_buffer():
    """If at least one bound browser receives the frame, it is delivered live
    and not buffered — a dead sibling socket doesn't trigger a replay."""
    rt = RoutingTable()
    dead, live = DeadWS(), FakeWS()
    await rt.add_browser(1, dead)
    await rt.bind_browser_session(1, dead, "tab-A")
    await rt.add_browser(1, live)
    await rt.bind_browser_session(1, live, "tab-A")

    delivered = await rt.route_to_session(1, "tab-A", "payload-1")
    assert delivered == 1

    later = FakeWS()
    await rt.add_browser(1, later)
    await rt.bind_browser_session(1, later, "tab-A")
    assert later.sent == []
