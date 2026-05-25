import pytest

from portal import db
from portal.beta import _rate_buckets


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    _rate_buckets.clear()
    yield
    _rate_buckets.clear()


@pytest.mark.asyncio
async def test_signup_creates_row(client):
    resp = await client.post(
        "/beta/signup",
        json={"email": "ann@example.com", "message": "interested in literature review"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "created": True}
    rows = await db.list_beta_signups()
    assert len(rows) == 1
    assert rows[0].email == "ann@example.com"
    assert rows[0].message == "interested in literature review"
    assert rows[0].source == "landing"


@pytest.mark.asyncio
async def test_signup_is_idempotent(client):
    r1 = await client.post("/beta/signup", json={"email": "ben@example.com"})
    r2 = await client.post("/beta/signup", json={"email": "BEN@example.com"})
    assert r1.json() == {"ok": True, "created": True}
    assert r2.json() == {"ok": True, "created": False}
    assert len(await db.list_beta_signups()) == 1


@pytest.mark.asyncio
async def test_signup_rejects_bad_email(client):
    resp = await client.post("/beta/signup", json={"email": "not-an-email"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_signup_accepts_optional_source(client):
    resp = await client.post(
        "/beta/signup",
        json={"email": "cara@example.com", "source": "twitter"},
    )
    assert resp.status_code == 200
    rows = await db.list_beta_signups()
    assert rows[0].source == "twitter"


@pytest.mark.asyncio
async def test_signup_rate_limit(client, monkeypatch):
    from portal.config import settings
    monkeypatch.setattr(settings, "rate_limit_per_min", 2)
    for i in range(2):
        r = await client.post("/beta/signup", json={"email": f"u{i}@example.com"})
        assert r.status_code == 200
    r = await client.post("/beta/signup", json={"email": "blocked@example.com"})
    assert r.status_code == 429


@pytest.mark.parametrize("bad_char", ["<", ">", "&", '"', "'"])
@pytest.mark.asyncio
async def test_signup_rejects_html_metachars_in_message(client, bad_char):
    resp = await client.post(
        "/beta/signup",
        json={"email": "dan@example.com", "message": f"hello {bad_char}there"},
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("bad_char", ["<", ">", "&", '"', "'"])
@pytest.mark.asyncio
async def test_signup_rejects_html_metachars_in_source(client, bad_char):
    resp = await client.post(
        "/beta/signup",
        json={"email": "eve@example.com", "source": f"x{bad_char}y"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_signup_coerces_malformed_xff_to_unknown(client):
    resp = await client.post(
        "/beta/signup",
        json={"email": "frank@example.com"},
        headers={"X-Forwarded-For": "<script>alert(1)</script>"},
    )
    assert resp.status_code == 200
    rows = await db.list_beta_signups()
    assert rows[0].email == "frank@example.com"
    assert rows[0].ip == "unknown"


@pytest.mark.asyncio
async def test_signup_keeps_well_shaped_xff(client):
    resp = await client.post(
        "/beta/signup",
        json={"email": "gina@example.com"},
        headers={"X-Forwarded-For": "203.0.113.5, 10.0.0.1"},
    )
    assert resp.status_code == 200
    rows = await db.list_beta_signups()
    assert rows[0].ip == "203.0.113.5"


@pytest.mark.asyncio
async def test_admin_beta_page_lists_signups(client):
    # Need an admin session to view; use the existing admin pattern.
    from portal import auth
    admin_user = await db.create_user("admin@example.com")
    await db.create_beta_signup(email="researcher@example.com", source="landing")
    cookie = auth.sign_session(admin_user.id)
    resp = await client.get(
        "/admin/beta",
        cookies={auth.SESSION_COOKIE: cookie},
    )
    assert resp.status_code == 200
    assert "researcher@example.com" in resp.text
