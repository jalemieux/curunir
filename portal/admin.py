import argparse
import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from portal import auth, csrf, db, email_send
from portal.config import settings
from portal.db import User
from portal.routing import routing


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


async def admin_user(user: User = Depends(auth.current_user)) -> User:
    if user.email.strip().lower() not in settings.admin_email_set:
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    return user


def _verify_csrf_form(user: User, csrf_token: str) -> None:
    if not csrf.verify_csrf(user.id, csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="CSRF check failed")


def _signin_link(token: str) -> str:
    return f"{settings.portal_base_url.rstrip('/')}/sign-in?token={token}"


async def _render_admin(request: Request, user: User, **extra):
    """Render admin.html with the shared context. `extra` overrides defaults
    (e.g. the new_* fields shown after a create/show action)."""
    ctx = {
        "users": await db.list_users(),
        "csrf_token": csrf.issue_csrf(user.id),
        "online_ids": routing.online_agent_user_ids(),
        "new_container_token": None,
        "new_signin_link": None,
        "new_user_email": None,
    }
    ctx.update(extra)
    return templates.TemplateResponse(request, "admin.html", ctx)


@router.get("", response_class=HTMLResponse)
async def admin_index(request: Request, user: User = Depends(admin_user)):
    return await _render_admin(request, user)


@router.post("/users", response_class=HTMLResponse)
async def admin_create_user(
    request: Request,
    email: str = Form(...),
    csrf_token: str = Form(..., alias="csrf"),
    user: User = Depends(admin_user),
):
    _verify_csrf_form(user, csrf_token)
    new_user = await db.create_user(email)
    signin_link = _signin_link(new_user.sign_in_token)
    await email_send.send_signin_email(new_user.email, signin_link)
    return await _render_admin(
        request, user,
        new_container_token=new_user.container_token,
        new_signin_link=signin_link,
        new_user_email=new_user.email,
    )


@router.post("/users/{user_id}/send-signin-email")
async def admin_send_signin_email(
    user_id: int,
    csrf_token: str = Form(..., alias="csrf"),
    user: User = Depends(admin_user),
):
    _verify_csrf_form(user, csrf_token)
    target = await db.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(404)
    await email_send.send_signin_email(target.email, _signin_link(target.sign_in_token))
    return RedirectResponse("/admin", status_code=303)


@router.post("/users/{user_id}/show-signin-link", response_class=HTMLResponse)
async def admin_show_signin_link(
    request: Request,
    user_id: int,
    csrf_token: str = Form(..., alias="csrf"),
    user: User = Depends(admin_user),
):
    _verify_csrf_form(user, csrf_token)
    target = await db.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(404)
    return await _render_admin(
        request, user,
        new_signin_link=_signin_link(target.sign_in_token),
        new_user_email=target.email,
    )


@router.post("/users/{user_id}/show-container-token", response_class=HTMLResponse)
async def admin_show_container_token(
    request: Request,
    user_id: int,
    csrf_token: str = Form(..., alias="csrf"),
    user: User = Depends(admin_user),
):
    _verify_csrf_form(user, csrf_token)
    target = await db.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(404)
    return await _render_admin(
        request, user,
        new_container_token=target.container_token,
        new_user_email=target.email,
    )


@router.post("/users/{user_id}/regenerate-sign-in")
async def admin_regenerate_sign_in(
    user_id: int,
    csrf_token: str = Form(..., alias="csrf"),
    user: User = Depends(admin_user),
):
    _verify_csrf_form(user, csrf_token)
    new_token = await db.regenerate_sign_in_token(user_id)
    target = await db.get_user_by_id(user_id)
    if target:
        await email_send.send_signin_email(target.email, _signin_link(new_token))
    return RedirectResponse("/admin", status_code=303)


@router.post("/users/{user_id}/regenerate-container")
async def admin_regenerate_container(
    user_id: int,
    csrf_token: str = Form(..., alias="csrf"),
    user: User = Depends(admin_user),
):
    _verify_csrf_form(user, csrf_token)
    await db.regenerate_container_token(user_id)
    return RedirectResponse("/admin", status_code=303)


@router.get("/beta", response_class=HTMLResponse)
async def admin_beta_signups(request: Request, user: User = Depends(admin_user)):
    signups = await db.list_beta_signups()
    lines = ["<!doctype html><meta charset=utf-8><title>Beta signups</title>",
             "<style>body{font:14px/1.5 -apple-system,sans-serif;padding:24px;color:#111}",
             "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;text-align:left;vertical-align:top}",
             "th{background:#f5f5f5}.empty{color:#888;padding:24px}</style>",
             "<h1>Beta signups</h1>"]
    if not signups:
        lines.append('<div class="empty">No signups yet.</div>')
    else:
        lines.append(f"<p>{len(signups)} total</p>")
        lines.append("<table><tr><th>When</th><th>Email</th><th>Source</th><th>Message</th><th>IP</th></tr>")
        for s in signups:
            lines.append(
                f"<tr><td>{s.created_at:%Y-%m-%d %H:%M}</td>"
                f"<td>{s.email}</td>"
                f"<td>{s.source or ''}</td>"
                f"<td>{(s.message or '')[:200]}</td>"
                f"<td>{s.ip or ''}</td></tr>"
            )
        lines.append("</table>")
    return HTMLResponse("\n".join(lines))


@router.post("/users/{user_id}/deactivate")
async def admin_deactivate(
    user_id: int,
    csrf_token: str = Form(..., alias="csrf"),
    user: User = Depends(admin_user),
):
    _verify_csrf_form(user, csrf_token)
    await db.deactivate_user(user_id)
    return RedirectResponse("/admin", status_code=303)


# ----- CLI: python -m portal.admin create-user --email user@example.com -----

async def _cli_create_user(email: str) -> None:
    await db.init_pool()
    await db.run_migrations()
    try:
        user = await db.create_user(email)
        await email_send.send_signin_email(user.email, _signin_link(user.sign_in_token))
        print(f"Created user {user.id} <{user.email}>")
        print(f"Container token (set in container env as CURUNIR_PORTAL_TOKEN):")
        print(f"  {user.container_token}")
        print(f"Sign-in link emailed; copy of link:")
        print(f"  {_signin_link(user.sign_in_token)}")
    finally:
        await db.close_pool()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m portal.admin")
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("create-user")
    create.add_argument("--email", required=True)
    args = parser.parse_args()
    if args.cmd == "create-user":
        asyncio.run(_cli_create_user(args.email))


if __name__ == "__main__":
    main()
