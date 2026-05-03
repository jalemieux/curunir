import pytest

from portal import auth, db


@pytest.mark.asyncio
async def test_root_redirects_when_unauth(client):
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/needs-invite"


@pytest.mark.asyncio
async def test_root_serves_chat_when_authed(client):
    user = await db.create_user("rooted@example.com")
    cookie = auth.sign_session(user.id)
    resp = await client.get("/", cookies={auth.SESSION_COOKIE: cookie})
    assert resp.status_code == 200
    assert b"<title>Curunir</title>" in resp.content
    assert b"/ws/browser" in resp.content
