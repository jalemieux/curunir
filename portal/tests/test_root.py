import pytest

from portal import auth, db


@pytest.mark.asyncio
async def test_root_serves_finance_landing_when_unauth(client):
    # Unauthenticated `/` is the public face of curunir.ai and serves the
    # finance landing page (the research assistant lives at /assistant). It is
    # not gated behind an invite redirect.
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    assert b"Your private financial analyst." in resp.content


@pytest.mark.asyncio
async def test_assistant_serves_research_landing(client):
    resp = await client.get("/assistant/", follow_redirects=False)
    assert resp.status_code == 200
    assert b"A private research assistant." in resp.content


@pytest.mark.asyncio
async def test_root_serves_chat_when_authed(client):
    user = await db.create_user("rooted@example.com")
    cookie = auth.sign_session(user.id)
    resp = await client.get("/", cookies={auth.SESSION_COOKIE: cookie})
    assert resp.status_code == 200
    assert b"<title>Curunir</title>" in resp.content
    assert b"/ws/browser" in resp.content
