import pytest

from portal import auth, csrf, db


async def _signed_cookie_for(email: str) -> tuple[int, dict]:
    user = await db.create_user(email)
    return user.id, {auth.SESSION_COOKIE: auth.sign_session(user.id)}


@pytest.mark.asyncio
async def test_admin_index_403_for_non_admin(client):
    _, cookies = await _signed_cookie_for("regular@example.com")
    resp = await client.get("/admin", cookies=cookies)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_index_renders_for_admin(client):
    _, cookies = await _signed_cookie_for("admin@example.com")
    resp = await client.get("/admin", cookies=cookies)
    assert resp.status_code == 200
    assert "Admin" in resp.text


@pytest.mark.asyncio
async def test_create_user_requires_csrf(client):
    user_id, cookies = await _signed_cookie_for("admin@example.com")
    resp = await client.post(
        "/admin/users",
        data={"email": "new@example.com", "csrf": "wrong"},
        cookies=cookies,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_user_with_valid_csrf_creates_and_shows_token(client):
    user_id, cookies = await _signed_cookie_for("admin@example.com")
    token = csrf.issue_csrf(user_id)
    resp = await client.post(
        "/admin/users",
        data={"email": "fresh@example.com", "csrf": token},
        cookies=cookies,
    )
    assert resp.status_code == 200
    assert "fresh@example.com" in resp.text
    assert "Container token" in resp.text
    assert "Sign-in link" in resp.text

    users = await db.list_users()
    fresh = next(u for u in users if u.email == "fresh@example.com")
    # Sign-in link in the response includes the user's sign_in_token verbatim
    assert fresh.sign_in_token in resp.text


@pytest.mark.asyncio
async def test_deactivate_marks_user_inactive(client):
    admin_id, cookies = await _signed_cookie_for("admin@example.com")
    target = await db.create_user("target@example.com")
    csrf_token = csrf.issue_csrf(admin_id)

    resp = await client.post(
        f"/admin/users/{target.id}/deactivate",
        data={"csrf": csrf_token},
        cookies=cookies,
        follow_redirects=False,
    )
    assert resp.status_code == 303

    after = await db.get_user_by_id(target.id)
    assert after.is_active is False


@pytest.mark.asyncio
async def test_show_signin_link_renders_link_for_existing_user(client):
    admin_id, cookies = await _signed_cookie_for("admin@example.com")
    target = await db.create_user("target@example.com")
    csrf_token = csrf.issue_csrf(admin_id)

    resp = await client.post(
        f"/admin/users/{target.id}/show-signin-link",
        data={"csrf": csrf_token},
        cookies=cookies,
    )
    assert resp.status_code == 200
    assert "Sign-in link" in resp.text
    assert target.sign_in_token in resp.text
    # Container token must NOT be re-shown via this flow
    assert target.container_token not in resp.text


@pytest.mark.asyncio
async def test_show_signin_link_requires_csrf(client):
    _, cookies = await _signed_cookie_for("admin@example.com")
    target = await db.create_user("target@example.com")
    resp = await client.post(
        f"/admin/users/{target.id}/show-signin-link",
        data={"csrf": "wrong"},
        cookies=cookies,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_show_container_token_renders_token_for_existing_user(client):
    admin_id, cookies = await _signed_cookie_for("admin@example.com")
    target = await db.create_user("target@example.com")
    csrf_token = csrf.issue_csrf(admin_id)

    resp = await client.post(
        f"/admin/users/{target.id}/show-container-token",
        data={"csrf": csrf_token},
        cookies=cookies,
    )
    assert resp.status_code == 200
    assert target.container_token in resp.text


@pytest.mark.asyncio
async def test_show_container_token_requires_csrf(client):
    _, cookies = await _signed_cookie_for("admin@example.com")
    target = await db.create_user("target@example.com")
    resp = await client.post(
        f"/admin/users/{target.id}/show-container-token",
        data={"csrf": "wrong"},
        cookies=cookies,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_show_container_token_unknown_user_404(client):
    admin_id, cookies = await _signed_cookie_for("admin@example.com")
    csrf_token = csrf.issue_csrf(admin_id)
    resp = await client.post(
        "/admin/users/999999/show-container-token",
        data={"csrf": csrf_token},
        cookies=cookies,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_email_compare_case_insensitive(client, monkeypatch):
    from portal.config import settings
    monkeypatch.setattr(settings, "admin_emails", "Admin@Example.Com")
    _, cookies = await _signed_cookie_for("admin@example.com")
    resp = await client.get("/admin", cookies=cookies)
    assert resp.status_code == 200


class _FakeAgent:
    """Sender-shaped stand-in for a connected curunir container."""
    async def send_text(self, data: str) -> None:
        pass

    async def close(self, code: int = 1000, reason: str = "") -> None:
        pass


def _row_for(html: str, email: str) -> str:
    """Return the <tr>...</tr> fragment containing the given email."""
    rows = html.split("<tr")
    return next(r for r in rows if email in r)


@pytest.mark.asyncio
async def test_admin_index_shows_agent_connection_status(client):
    from portal.routing import routing

    connected = await db.create_user("connected@example.com")
    offline = await db.create_user("offline@example.com")
    _, cookies = await _signed_cookie_for("admin@example.com")

    agent = _FakeAgent()
    await routing.register_agent(connected.id, agent)
    try:
        resp = await client.get("/admin", cookies=cookies)
    finally:
        await routing.unregister_agent(connected.id, agent)

    assert resp.status_code == 200
    assert "online" in _row_for(resp.text, "connected@example.com")
    assert "offline" in _row_for(resp.text, "offline@example.com")


@pytest.mark.asyncio
async def test_admin_beta_page_escapes_html_in_signup_fields(client):
    """Stored XSS regression test: attacker-controlled signup fields must be
    HTML-escaped when rendered on the admin beta page."""
    from portal import auth

    admin = await db.create_user("admin@example.com")
    await db.create_beta_signup(
        email="x@y.z",
        message="<script>alert(1)</script>",
        source='"><img src=x onerror=1>',
        ip="<svg/onload=1>",
    )
    cookie = auth.sign_session(admin.id)
    resp = await client.get(
        "/admin/beta",
        cookies={auth.SESSION_COOKIE: cookie},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "onerror=1>" not in body
    assert "&quot;" in body or "&#34;" in body
    assert "&lt;svg/onload=1&gt;" in body
