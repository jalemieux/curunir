import pytest

from portal import auth, db


@pytest.mark.asyncio
async def test_root_serves_homepage_when_unauth(client):
    # Unauthenticated `/` is the public face of curunir.ai: the homepage that
    # lists every role. It is not gated behind an invite redirect.
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    body = resp.content
    assert b"Your AI agent lives on your desk." in body
    for role in (
        b"Financial analyst",
        b"Life coach",
        b"Career coach",
        b"Medical research",
        b"Go-to-market strategist",
        b"General assistant",
    ):
        assert role in body
    # Sign-ups from this page are segmented as source="home".
    assert b"source: 'home'" in body


@pytest.mark.asyncio
async def test_homepage_device_photo_is_served(client):
    page = await client.get("/", follow_redirects=False)
    assert b'src="/home/device-desk.jpg"' in page.content
    resp = await client.get("/home/device-desk.jpg", follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"


@pytest.mark.asyncio
async def test_finance_landing_still_served_at_finance(client):
    # The homepage links to it; it used to be what `/` served.
    resp = await client.get("/finance/", follow_redirects=False)
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


@pytest.mark.asyncio
async def test_variant_pages_served_under_v(client):
    # Issue #544: positioning-test variants live at static/v/<slug>/ and are
    # served at /v/<slug>/ with no per-variant portal code.
    from portal.app import _VARIANTS_DIR

    slug = "_test-variant"
    page = _VARIANTS_DIR / slug / "index.html"
    page.parent.mkdir()
    try:
        page.write_text("<h1>variant under test</h1>")
        resp = await client.get(f"/v/{slug}/", follow_redirects=False)
        assert resp.status_code == 200
        assert b"variant under test" in resp.content
    finally:
        page.unlink()
        page.parent.rmdir()

    resp = await client.get(f"/v/{slug}/", follow_redirects=False)
    assert resp.status_code == 404
