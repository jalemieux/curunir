from pathlib import Path

import pytest

from portal import auth, db


_MOBILE_HTML = Path(__file__).resolve().parents[1] / "static" / "mobile.html"

# A representative iPhone Safari UA — matches the mobile redirect heuristic.
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
# A desktop macOS Safari UA — must NOT trigger the mobile redirect.
DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


async def _auth_cookie() -> dict[str, str]:
    user = await db.create_user("mobile@example.com")
    return {auth.SESSION_COOKIE: auth.sign_session(user.id)}


# --- /m route ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_mobile_requires_auth(client):
    resp = await client.get("/m")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_mobile_serves_shell_when_authed(client):
    resp = await client.get("/m", cookies=await _auth_cookie())
    assert resp.status_code == 200
    body = resp.content
    # Reuses the existing browser WS protocol — no backend change.
    assert b"/ws/browser" in body
    # Viewport matches desktop: device-width + safe-area, no zoom lock (#355).
    # iOS ignores user-scalable=no anyway; the real double-tap-zoom fix is
    # touch-action on controls, and locking zoom only harms accessibility.
    assert b"width=device-width" in body
    assert b"viewport-fit=cover" in body
    assert b"user-scalable=no" not in body
    # PWA shell from day one.
    assert b"/manifest.json" in body
    assert b"apple-mobile-web-app-capable" in body


# --- PWA manifest -----------------------------------------------------------

@pytest.mark.asyncio
async def test_manifest_served(client):
    # The manifest must be reachable without auth so add-to-homescreen works
    # before the session cookie is present.
    resp = await client.get("/manifest.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["display"] == "standalone"
    assert data["start_url"] == "/m"


# --- UA-based redirect on / -------------------------------------------------

@pytest.mark.asyncio
async def test_root_mobile_ua_redirects_to_m(client):
    resp = await client.get(
        "/",
        cookies=await _auth_cookie(),
        headers={"user-agent": IPHONE_UA},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/m"


@pytest.mark.asyncio
async def test_root_mobile_ua_desktop_param_serves_desktop(client):
    # ?desktop=1 is the escape hatch — force the desktop UI on a phone.
    resp = await client.get(
        "/?desktop=1",
        cookies=await _auth_cookie(),
        headers={"user-agent": IPHONE_UA},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert b"<title>Curunir</title>" in resp.content


@pytest.mark.asyncio
async def test_root_desktop_ua_serves_desktop(client):
    resp = await client.get(
        "/",
        cookies=await _auth_cookie(),
        headers={"user-agent": DESKTOP_UA},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert b"<title>Curunir</title>" in resp.content


@pytest.mark.asyncio
async def test_root_mobile_ua_unauth_does_not_redirect_to_m(client):
    # An unauthenticated phone visitor must land on the public page, never be
    # bounced to /m (which 401s) — that would trap them with no next step.
    resp = await client.get(
        "/",
        headers={"user-agent": IPHONE_UA},
        follow_redirects=False,
    )
    assert resp.status_code != 307
    assert resp.headers.get("location") != "/m"


# --- Static shell guards ----------------------------------------------------

def test_mobile_shell_disables_double_tap_zoom():
    # Bug #355(1): the first tap on a control did nothing because iOS Safari
    # held it ~300ms waiting for a double-tap-to-zoom. The fix opts tappable
    # controls out of the zoom gesture with `touch-action: manipulation`
    # (which also removes the click delay) instead of the OS-ignored
    # `user-scalable=no` / `maximum-scale` viewport flags.
    src = _MOBILE_HTML.read_text()
    assert "touch-action: manipulation" in src
    assert "user-scalable=no" not in src
    assert "maximum-scale" not in src


def test_mobile_shell_is_conversation_list_first():
    # The mobile home view is the conversation list (problem #2): the shell
    # must request the conversation list on connect.
    src = _MOBILE_HTML.read_text()
    assert "conversations_request" in src


def test_mobile_shell_rebootstraps_on_agent_online():
    # Bug #355(2): the browser↔portal socket opens before the agent container
    # has dialed in, so the bootstrap frames sent in ws.onopen are forwarded
    # to nothing and dropped — leaving blank list/chat shells. Mirror the
    # desktop #334 fix: a bootstrapSync() helper re-issued on the agent's
    # offline→online edge, deduped by a bootstrappedOnline epoch flag.
    src = _MOBILE_HTML.read_text()
    assert "bootstrapSync" in src
    assert "bootstrappedOnline" in src
