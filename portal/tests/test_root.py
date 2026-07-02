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
async def test_assistant_landing_highlights_open_source_and_zero_retention(client):
    # Issue #473: the "where it lives" section must communicate that the models
    # are open-source, comparable in quality to frontier systems, and served
    # from the cloud with zero data retention.
    resp = await client.get("/assistant/", follow_redirects=False)
    assert resp.status_code == 200
    body = resp.content
    assert b"open-source models" in body
    assert b"frontier" in body  # frontier-comparable quality claim
    assert b"zero data retention" in body
    # The old "mix of frontier models" framing (which implied proprietary
    # vendor models) must be gone.
    assert b"mix of frontier models" not in body


@pytest.mark.asyncio
async def test_root_serves_chat_when_authed(client):
    user = await db.create_user("rooted@example.com")
    cookie = auth.sign_session(user.id)
    resp = await client.get("/", cookies={auth.SESSION_COOKIE: cookie})
    assert resp.status_code == 200
    assert b"<title>Curunir</title>" in resp.content
    assert b"/ws/browser" in resp.content
