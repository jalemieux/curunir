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

    users = await db.list_users()
    assert any(u.email == "fresh@example.com" for u in users)


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
async def test_admin_email_compare_case_insensitive(client, monkeypatch):
    from portal.config import settings
    monkeypatch.setattr(settings, "admin_emails", "Admin@Example.Com")
    _, cookies = await _signed_cookie_for("admin@example.com")
    resp = await client.get("/admin", cookies=cookies)
    assert resp.status_code == 200
