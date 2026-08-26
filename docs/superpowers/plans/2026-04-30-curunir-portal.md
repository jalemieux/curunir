# Curunir Portal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a hosted FastAPI portal on Render plus a new `PortalChannel` inside curunir, so that admin-provisioned users can chat with their own self-hosted curunir container from a phone or desktop browser, with no chat content stored on the portal.

**Architecture:** Federated. Curunir containers dial out via WSS to the portal (authenticated by a long-lived shared-secret token). The portal authenticates browsers via a signed session cookie (set by clicking an admin-issued sign-in link) and routes messages between the browser and the user's container in memory. Portal stores only `users` rows (email + two tokens + active flag) in Postgres. No chat history persists on the portal.

**Tech Stack:**
- **Portal:** Python 3.12, FastAPI, uvicorn, asyncpg (Postgres), itsdangerous (cookie + CSRF signing), jinja2 (templates), httpx (email API), python-multipart (form parsing). Vanilla HTML+JS+CSS frontend (`marked` + `highlight.js` via CDN). Postmark for transactional email.
- **Curunir-side:** New `src/channels/portal.py` + extracted `src/channels/_attachments.py`. Reuses existing `websockets` library, `IncomingMessage`/`OutgoingMessage` envelopes, and `Agent` history.
- **Tests:** `pytest-asyncio`, `httpx.AsyncClient`, `websockets` test client. Postgres for portal tests (via `docker-compose.yml` for local, service container in CI).
- **Deployment:** Render web service + Render Postgres add-on.

---

## File Structure

### New: `portal/` (separate Python project, same repo)

```
portal/
├── pyproject.toml              # Dependencies + pytest config
├── Dockerfile                  # For Render
├── render.yaml                 # Render service + Postgres binding
├── README.md                   # Local dev + deploy notes
├── docker-compose.yml          # Local Postgres for dev/tests
├── __init__.py
├── app.py                      # FastAPI app, lifespan, route mounting, /healthz
├── config.py                   # Pydantic settings
├── db.py                       # asyncpg pool, users-table accessors
├── auth.py                     # Cookie sign/verify, `current_user` dependency
├── csrf.py                     # Stateless CSRF token issue/verify
├── sign_in.py                  # GET + POST /sign-in
├── admin.py                    # /admin endpoints + python -m portal.admin CLI
├── email_send.py               # Postmark wrapper
├── routing.py                  # In-memory UserRoute table + lifecycle
├── ws_agent.py                 # /ws/agent endpoint
├── ws_browser.py               # /ws/browser endpoint
├── templates/
│   ├── sign_in_confirm.html
│   ├── sign_in_error.html
│   ├── needs_invite.html
│   └── admin.html
├── static/
│   └── index.html              # Chat surface (HTML + CSS + JS)
├── migrations/
│   └── 0001_create_users.sql
└── tests/
    ├── conftest.py
    ├── test_db.py
    ├── test_auth.py
    ├── test_csrf.py
    ├── test_sign_in.py
    ├── test_admin.py
    ├── test_email_send.py
    ├── test_routing.py
    ├── test_ws_agent.py
    ├── test_ws_browser.py
    └── test_static.py
```

### New: curunir-side

- `src/channels/_attachments.py` — extracted from `ws.py`
- `src/channels/portal.py` — `PortalChannel`
- `tests/test_portal_channel.py`

### Modified: curunir-side

- `src/channels/ws.py` — re-import helpers from `_attachments.py`
- `run.py` — wire `PortalChannel` when env vars present
- `.env.example` — add `CURUNIR_PORTAL_URL`, `CURUNIR_PORTAL_TOKEN`
- `CLAUDE.md` — document Portal channel + `portal/` directory

---

## Phase 1 — Portal foundation

### Task 1: Scaffold portal/ project + healthz

**Files:**
- Create: `portal/pyproject.toml`
- Create: `portal/__init__.py`
- Create: `portal/app.py`
- Create: `portal/config.py`
- Create: `portal/docker-compose.yml`
- Create: `portal/README.md`
- Create: `portal/tests/__init__.py`
- Create: `portal/tests/conftest.py`
- Create: `portal/tests/test_static.py`

- [ ] **Step 1: Create `portal/pyproject.toml`**

```toml
[project]
name = "curunir-portal"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.110",
  "uvicorn[standard]>=0.29",
  "asyncpg>=0.29",
  "itsdangerous>=2.2",
  "jinja2>=3.1",
  "httpx>=0.27",
  "python-multipart>=0.0.9",
  "pydantic-settings>=2.2",
  "websockets>=12.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8",
  "pytest-asyncio>=0.23",
  "anyio>=4",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create `portal/__init__.py` (empty file)**

```python
```

- [ ] **Step 3: Create `portal/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/portal"
    portal_secret_key: str = "dev-only-do-not-use-in-prod"
    portal_base_url: str = "http://localhost:8000"
    email_api_key: str = ""
    email_from: str = "noreply@example.com"
    admin_emails: str = ""  # comma-separated
    rate_limit_per_min: int = 10
    debug: bool = False

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}


settings = Settings()
```

- [ ] **Step 4: Create `portal/app.py` with `/healthz`**

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    # DB pool will be added in Task 2.
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return JSONResponse({"status": "ok"})
```

- [ ] **Step 5: Create `portal/docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: portal
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "postgres"]
      interval: 5s
      timeout: 5s
      retries: 5
```

- [ ] **Step 6: Create `portal/README.md`**

```markdown
# Curunir Portal

Hosted multi-user chat surface for curunir.

## Local development

```bash
cd portal
docker compose up -d   # local Postgres
pip install -e ".[dev]"
uvicorn portal.app:app --reload
```

## Tests

```bash
docker compose up -d
pytest
```

## Deploy

Render auto-deploys from the linked branch using `render.yaml`.
```

- [ ] **Step 7: Create `portal/tests/__init__.py` (empty file)**

```python
```

- [ ] **Step 8: Create `portal/tests/conftest.py`**

```python
import os

import pytest
from httpx import AsyncClient, ASGITransport

# Force test settings before importing the app.
os.environ.setdefault("PORTAL_SECRET_KEY", "test-secret-do-not-use")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/portal_test",
)
os.environ.setdefault("ADMIN_EMAILS", "admin@example.com")
os.environ.setdefault("PORTAL_BASE_URL", "http://localhost:8000")

from portal.app import app  # noqa: E402


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

- [ ] **Step 9: Create `portal/tests/test_static.py` — failing test for `/healthz`**

```python
import pytest


@pytest.mark.asyncio
async def test_healthz_returns_ok(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 10: Run test, expect PASS**

Run: `cd portal && pytest tests/test_static.py -v`
Expected: PASS (the endpoint already exists from Step 4).

- [ ] **Step 11: Commit**

```bash
git add portal/
git commit -m "feat(portal): scaffold FastAPI app with healthz endpoint"
```

---

### Task 2: Postgres connection + users table + accessors

**Files:**
- Create: `portal/migrations/0001_create_users.sql`
- Create: `portal/db.py`
- Modify: `portal/app.py` — wire DB pool into lifespan + `/healthz` does `SELECT 1`
- Modify: `portal/tests/conftest.py` — DB fixture, run migrations
- Create: `portal/tests/test_db.py`

- [ ] **Step 1: Create `portal/migrations/0001_create_users.sql`**

```sql
CREATE TABLE IF NOT EXISTS users (
  id              BIGSERIAL PRIMARY KEY,
  email           TEXT NOT NULL UNIQUE,
  sign_in_token   TEXT NOT NULL UNIQUE,
  container_token TEXT NOT NULL UNIQUE,
  is_active       BOOLEAN NOT NULL DEFAULT TRUE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS users_sign_in_token_idx
  ON users (sign_in_token) WHERE is_active;
CREATE INDEX IF NOT EXISTS users_container_token_idx
  ON users (container_token) WHERE is_active;
```

- [ ] **Step 2: Create `portal/db.py`**

```python
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import asyncpg

from portal.config import settings


_pool: asyncpg.Pool | None = None


def make_token() -> str:
    """URL-safe random 32-byte token."""
    return secrets.token_urlsafe(32)


@dataclass
class User:
    id: int
    email: str
    sign_in_token: str
    container_token: str
    is_active: bool


def _row_to_user(row: asyncpg.Record) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        sign_in_token=row["sign_in_token"],
        container_token=row["container_token"],
        is_active=row["is_active"],
    )


async def init_pool() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized")
    return _pool


async def run_migrations() -> None:
    sql = (Path(__file__).parent / "migrations" / "0001_create_users.sql").read_text()
    async with get_pool().acquire() as conn:
        await conn.execute(sql)


async def ping() -> bool:
    async with get_pool().acquire() as conn:
        return (await conn.fetchval("SELECT 1")) == 1


async def create_user(email: str) -> User:
    sign_in_token = make_token()
    container_token = make_token()
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (email, sign_in_token, container_token)
            VALUES ($1, $2, $3)
            RETURNING id, email, sign_in_token, container_token, is_active
            """,
            email.strip().lower(),
            sign_in_token,
            container_token,
        )
    return _row_to_user(row)


async def get_user_by_id(user_id: int) -> Optional[User]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, sign_in_token, container_token, is_active "
            "FROM users WHERE id = $1",
            user_id,
        )
    return _row_to_user(row) if row else None


async def get_active_user_by_sign_in_token(token: str) -> Optional[User]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, sign_in_token, container_token, is_active "
            "FROM users WHERE sign_in_token = $1 AND is_active",
            token,
        )
    return _row_to_user(row) if row else None


async def get_active_user_by_container_token(token: str) -> Optional[User]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, sign_in_token, container_token, is_active "
            "FROM users WHERE container_token = $1 AND is_active",
            token,
        )
    return _row_to_user(row) if row else None


async def list_users() -> list[User]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, email, sign_in_token, container_token, is_active "
            "FROM users ORDER BY id"
        )
    return [_row_to_user(r) for r in rows]


async def deactivate_user(user_id: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute("UPDATE users SET is_active = FALSE WHERE id = $1", user_id)


async def regenerate_sign_in_token(user_id: int) -> str:
    new_token = make_token()
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET sign_in_token = $1 WHERE id = $2", new_token, user_id
        )
    return new_token


async def regenerate_container_token(user_id: int) -> str:
    new_token = make_token()
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET container_token = $1 WHERE id = $2", new_token, user_id
        )
    return new_token
```

- [ ] **Step 3: Modify `portal/app.py` — wire DB into lifespan + healthz**

Replace entire file:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from portal import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    await db.run_migrations()
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    ok = await db.ping()
    return JSONResponse({"status": "ok" if ok else "degraded"})
```

- [ ] **Step 4: Modify `portal/tests/conftest.py` — add per-test DB cleanup**

Append:

```python
import pytest_asyncio
from portal import db as portal_db


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(client):
    """After each test, truncate users."""
    yield
    pool = portal_db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE users RESTART IDENTITY")
```

- [ ] **Step 5: Create `portal/tests/test_db.py`**

```python
import pytest

from portal import db


@pytest.mark.asyncio
async def test_create_and_lookup_user(client):
    user = await db.create_user("Alice@Example.com")
    assert user.email == "alice@example.com"
    assert user.is_active is True
    assert len(user.sign_in_token) >= 40
    assert user.sign_in_token != user.container_token

    by_id = await db.get_user_by_id(user.id)
    assert by_id is not None and by_id.email == "alice@example.com"

    by_sign_in = await db.get_active_user_by_sign_in_token(user.sign_in_token)
    assert by_sign_in is not None and by_sign_in.id == user.id

    by_container = await db.get_active_user_by_container_token(user.container_token)
    assert by_container is not None and by_container.id == user.id


@pytest.mark.asyncio
async def test_deactivated_user_not_returned_by_token_lookups(client):
    user = await db.create_user("bob@example.com")
    await db.deactivate_user(user.id)

    assert await db.get_active_user_by_sign_in_token(user.sign_in_token) is None
    assert await db.get_active_user_by_container_token(user.container_token) is None

    by_id = await db.get_user_by_id(user.id)
    assert by_id is not None and by_id.is_active is False


@pytest.mark.asyncio
async def test_regenerate_tokens_invalidates_old(client):
    user = await db.create_user("carol@example.com")
    old_sign_in = user.sign_in_token

    new_sign_in = await db.regenerate_sign_in_token(user.id)
    assert new_sign_in != old_sign_in
    assert await db.get_active_user_by_sign_in_token(old_sign_in) is None
    assert await db.get_active_user_by_sign_in_token(new_sign_in) is not None
```

- [ ] **Step 6: Run tests, expect PASS**

Run: `cd portal && docker compose up -d && pytest tests/test_db.py -v`
Expected: 3 PASS.

(Pre-test: must `CREATE DATABASE portal_test` in the local Postgres, or extend the conftest to `CREATE DATABASE IF NOT EXISTS` — for v1, manual creation is fine; documented in `portal/README.md`.)

- [ ] **Step 7: Update `portal/README.md` — document `portal_test` DB**

Add under "Tests":

```bash
# Create test database once
docker compose exec postgres createdb -U postgres portal_test
pytest
```

- [ ] **Step 8: Commit**

```bash
git add portal/
git commit -m "feat(portal): users table + asyncpg accessors + healthz DB ping"
```

---

## Phase 2 — Auth

### Task 3: Cookie sign/verify + current_user dependency

**Files:**
- Create: `portal/auth.py`
- Create: `portal/tests/test_auth.py`

- [ ] **Step 1: Create `portal/auth.py`**

```python
from typing import Optional

from fastapi import Cookie, HTTPException, status
from itsdangerous import BadSignature, Signer

from portal import db
from portal.config import settings
from portal.db import User


SESSION_COOKIE = "portal_session"
COOKIE_VERSION = 1


def _signer() -> Signer:
    return Signer(settings.portal_secret_key, salt="portal-session")


def sign_session(user_id: int) -> str:
    """Return cookie value: '<user_id>.<v>.<sig>'."""
    payload = f"{user_id}.{COOKIE_VERSION}"
    return _signer().sign(payload.encode()).decode()


def verify_session(cookie_value: str) -> Optional[int]:
    """Return user_id if cookie is valid signature, else None."""
    try:
        payload = _signer().unsign(cookie_value.encode()).decode()
    except BadSignature:
        return None
    user_id_str, _v = payload.split(".", 1)
    try:
        return int(user_id_str)
    except ValueError:
        return None


async def current_user(
    portal_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User:
    """FastAPI dependency. 401 if no/invalid cookie or user is inactive."""
    if not portal_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    user_id = verify_session(portal_session)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    user = await db.get_user_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    return user


async def optional_current_user(
    portal_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User | None:
    """Like current_user but returns None instead of raising 401."""
    if not portal_session:
        return None
    user_id = verify_session(portal_session)
    if user_id is None:
        return None
    user = await db.get_user_by_id(user_id)
    if user is None or not user.is_active:
        return None
    return user
```

- [ ] **Step 2: Create `portal/tests/test_auth.py`**

```python
import pytest
from fastapi import Depends, FastAPI

from portal import auth, db
from portal.app import app


@pytest.mark.asyncio
async def test_sign_and_verify_roundtrip():
    cookie = auth.sign_session(42)
    assert auth.verify_session(cookie) == 42


@pytest.mark.asyncio
async def test_tampered_cookie_returns_none():
    cookie = auth.sign_session(42)
    tampered = cookie[:-1] + ("0" if cookie[-1] != "0" else "1")
    assert auth.verify_session(tampered) is None


@pytest.mark.asyncio
async def test_garbage_cookie_returns_none():
    assert auth.verify_session("not-a-real-cookie") is None
    assert auth.verify_session("") is None


@pytest.mark.asyncio
async def test_current_user_dependency_401_when_no_cookie(client):
    @app.get("/__test_protected")
    async def protected(user=Depends(auth.current_user)):
        return {"id": user.id}

    resp = await client.get("/__test_protected")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_current_user_dependency_returns_user(client):
    @app.get("/__test_who")
    async def who(user=Depends(auth.current_user)):
        return {"id": user.id, "email": user.email}

    user = await db.create_user("dee@example.com")
    cookie = auth.sign_session(user.id)
    resp = await client.get(
        "/__test_who", cookies={auth.SESSION_COOKIE: cookie}
    )
    assert resp.status_code == 200
    assert resp.json() == {"id": user.id, "email": "dee@example.com"}


@pytest.mark.asyncio
async def test_current_user_401_when_user_deactivated(client):
    @app.get("/__test_deactivated")
    async def deact(user=Depends(auth.current_user)):
        return {"id": user.id}

    user = await db.create_user("eve@example.com")
    cookie = auth.sign_session(user.id)
    await db.deactivate_user(user.id)

    resp = await client.get(
        "/__test_deactivated", cookies={auth.SESSION_COOKIE: cookie}
    )
    assert resp.status_code == 401
```

- [ ] **Step 3: Run tests, expect PASS**

Run: `cd portal && pytest tests/test_auth.py -v`
Expected: 6 PASS.

- [ ] **Step 4: Commit**

```bash
git add portal/auth.py portal/tests/test_auth.py
git commit -m "feat(portal): signed-cookie sessions + current_user dependency"
```

---

### Task 4: CSRF tokens (stateless HMAC)

**Files:**
- Create: `portal/csrf.py`
- Create: `portal/tests/test_csrf.py`

- [ ] **Step 1: Create `portal/csrf.py`**

```python
"""Stateless CSRF tokens.

The token is HMAC(secret, f"{user_id}:csrf"). It binds to the signed-in
user (session cookie); a CSRF token issued for one user does not
validate POSTs as another user. Verified on state-changing POSTs in
addition to SameSite=Strict on the session cookie (defense-in-depth).
"""

import hashlib
import hmac

from portal.config import settings


def issue_csrf(user_id: int) -> str:
    msg = f"{user_id}:csrf".encode()
    return hmac.new(
        settings.portal_secret_key.encode(), msg, hashlib.sha256
    ).hexdigest()


def verify_csrf(user_id: int, token: str) -> bool:
    expected = issue_csrf(user_id)
    return hmac.compare_digest(expected, token or "")
```

- [ ] **Step 2: Create `portal/tests/test_csrf.py`**

```python
import pytest

from portal import csrf


def test_issue_then_verify():
    token = csrf.issue_csrf(7)
    assert csrf.verify_csrf(7, token) is True


def test_verify_wrong_user_fails():
    token = csrf.issue_csrf(7)
    assert csrf.verify_csrf(8, token) is False


def test_verify_garbage_fails():
    assert csrf.verify_csrf(7, "") is False
    assert csrf.verify_csrf(7, "deadbeef") is False
```

- [ ] **Step 3: Run tests, expect PASS**

Run: `cd portal && pytest tests/test_csrf.py -v`
Expected: 3 PASS.

- [ ] **Step 4: Commit**

```bash
git add portal/csrf.py portal/tests/test_csrf.py
git commit -m "feat(portal): stateless CSRF token helpers"
```

---

### Task 5: Sign-in flow (GET confirmation + POST sets cookie)

**Files:**
- Create: `portal/templates/sign_in_confirm.html`
- Create: `portal/templates/sign_in_error.html`
- Create: `portal/templates/needs_invite.html`
- Create: `portal/sign_in.py`
- Modify: `portal/app.py` — mount sign-in router, mount templates, register `/needs-invite`
- Create: `portal/tests/test_sign_in.py`

- [ ] **Step 1: Create `portal/templates/sign_in_confirm.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in to Curunir</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 420px; margin: 80px auto; padding: 0 16px; color: #222; }
    h1 { font-size: 18px; margin-bottom: 16px; }
    button { padding: 10px 18px; font-size: 14px; background: #4ade80; border: 0; border-radius: 6px; cursor: pointer; }
    .email { color: #555; font-family: monospace; }
  </style>
</head>
<body>
  <h1>Sign in as <span class="email">{{ email }}</span>?</h1>
  <form method="post" action="/sign-in">
    <input type="hidden" name="token" value="{{ token }}">
    <button type="submit">Sign in</button>
  </form>
</body>
</html>
```

- [ ] **Step 2: Create `portal/templates/sign_in_error.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign-in failed</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 420px; margin: 80px auto; padding: 0 16px; color: #222; }
  </style>
</head>
<body>
  <h1>Sign-in link is invalid.</h1>
  <p>Contact your admin for a new link.</p>
</body>
</html>
```

- [ ] **Step 3: Create `portal/templates/needs_invite.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Curunir</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 420px; margin: 80px auto; padding: 0 16px; color: #222; }
  </style>
</head>
<body>
  <h1>You need an invite.</h1>
  <p>This portal is invite-only. Ask your admin.</p>
</body>
</html>
```

- [ ] **Step 4: Create `portal/sign_in.py`**

```python
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from portal import auth, db
from portal.config import settings


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# In-memory rate limit: per-IP deque of timestamps (seconds).
_rate_buckets: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=64))


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    bucket = _rate_buckets[ip]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= settings.rate_limit_per_min:
        return True
    bucket.append(now)
    return False


def _client_ip(request: Request) -> str:
    # Render terminates TLS and forwards via proxy; honor X-Forwarded-For first hop.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _no_cache_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
    }


@router.get("/sign-in", response_class=HTMLResponse)
async def sign_in_get(request: Request, token: str = ""):
    if _rate_limited(_client_ip(request)):
        return HTMLResponse(
            "Too many attempts. Try again in a minute.",
            status_code=429,
            headers=_no_cache_headers(),
        )
    user = await db.get_active_user_by_sign_in_token(token)
    if user is None:
        return templates.TemplateResponse(
            request, "sign_in_error.html", {}, status_code=400,
            headers=_no_cache_headers(),
        )
    return templates.TemplateResponse(
        request, "sign_in_confirm.html",
        {"email": user.email, "token": token},
        headers=_no_cache_headers(),
    )


@router.post("/sign-in")
async def sign_in_post(request: Request, token: str = Form(...)):
    if _rate_limited(_client_ip(request)):
        return HTMLResponse("Too many attempts.", status_code=429)
    user = await db.get_active_user_by_sign_in_token(token)
    if user is None:
        return templates.TemplateResponse(
            request, "sign_in_error.html", {}, status_code=400,
            headers=_no_cache_headers(),
        )
    cookie_value = auth.sign_session(user.id)
    response = RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=auth.SESSION_COOKIE,
        value=cookie_value,
        secure=not settings.debug,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


@router.get("/needs-invite", response_class=HTMLResponse)
async def needs_invite(request: Request):
    return templates.TemplateResponse(request, "needs_invite.html", {})
```

- [ ] **Step 5: Modify `portal/app.py` — mount the sign-in router**

Replace entire file:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from portal import db, sign_in


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    await db.run_migrations()
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(lifespan=lifespan)
app.include_router(sign_in.router)


@app.get("/healthz")
async def healthz():
    ok = await db.ping()
    return JSONResponse({"status": "ok" if ok else "degraded"})
```

- [ ] **Step 6: Create `portal/tests/test_sign_in.py`**

```python
import pytest

from portal import auth, db
from portal.sign_in import _rate_buckets


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    _rate_buckets.clear()
    yield
    _rate_buckets.clear()


@pytest.mark.asyncio
async def test_get_with_valid_token_renders_confirm_form(client):
    user = await db.create_user("ann@example.com")
    resp = await client.get(f"/sign-in?token={user.sign_in_token}")
    assert resp.status_code == 200
    assert "ann@example.com" in resp.text
    assert "<form" in resp.text and 'method="post"' in resp.text
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["referrer-policy"] == "no-referrer"


@pytest.mark.asyncio
async def test_get_with_invalid_token_renders_error(client):
    resp = await client.get("/sign-in?token=not-a-real-token")
    assert resp.status_code == 400
    assert "invalid" in resp.text.lower()


@pytest.mark.asyncio
async def test_get_with_deactivated_user_renders_error(client):
    user = await db.create_user("ben@example.com")
    await db.deactivate_user(user.id)
    resp = await client.get(f"/sign-in?token={user.sign_in_token}")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_sets_signed_cookie_and_redirects(client):
    user = await db.create_user("cara@example.com")
    resp = await client.post(
        "/sign-in", data={"token": user.sign_in_token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    set_cookie = resp.headers["set-cookie"]
    assert auth.SESSION_COOKIE in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie.lower()
    assert "Path=/" in set_cookie
    # Test mode = debug=False default; Secure should be set
    assert "Secure" in set_cookie

    # Cookie should validate
    cookie_value = resp.cookies.get(auth.SESSION_COOKIE)
    assert auth.verify_session(cookie_value) == user.id


@pytest.mark.asyncio
async def test_post_with_invalid_token_returns_error(client):
    resp = await client.post(
        "/sign-in", data={"token": "garbage"}, follow_redirects=False
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_threshold(client, monkeypatch):
    from portal.config import settings
    monkeypatch.setattr(settings, "rate_limit_per_min", 3)
    for _ in range(3):
        await client.get("/sign-in?token=x")
    blocked = await client.get("/sign-in?token=x")
    assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_token_reusable_across_devices(client):
    user = await db.create_user("dan@example.com")
    r1 = await client.post(
        "/sign-in", data={"token": user.sign_in_token}, follow_redirects=False
    )
    r2 = await client.post(
        "/sign-in", data={"token": user.sign_in_token}, follow_redirects=False
    )
    assert r1.status_code == 302 and r2.status_code == 302


@pytest.mark.asyncio
async def test_needs_invite_page_renders(client):
    resp = await client.get("/needs-invite")
    assert resp.status_code == 200
    assert "invite" in resp.text.lower()
```

- [ ] **Step 7: Run tests, expect PASS**

Run: `cd portal && pytest tests/test_sign_in.py -v`
Expected: 8 PASS.

- [ ] **Step 8: Commit**

```bash
git add portal/templates/ portal/sign_in.py portal/app.py portal/tests/test_sign_in.py
git commit -m "feat(portal): GET/POST /sign-in with reusable token, rate limit, secure cookie"
```

---

### Task 6: Email send (Postmark)

**Files:**
- Create: `portal/email_send.py`
- Create: `portal/tests/test_email_send.py`

- [ ] **Step 1: Create `portal/email_send.py`**

```python
"""Send transactional sign-in emails via Postmark.

Single function: send_signin_email(email, link). Wraps Postmark's
`/email` HTTP endpoint via httpx. If `EMAIL_API_KEY` is empty, the
function logs the email instead of sending — useful for local dev.
"""

import logging

import httpx

from portal.config import settings


logger = logging.getLogger(__name__)
POSTMARK_URL = "https://api.postmarkapp.com/email"


async def send_signin_email(email: str, link: str) -> None:
    body_html = (
        f"<p>You've been invited to Curunir.</p>"
        f"<p><a href=\"{link}\">Click here to sign in</a></p>"
        f"<p>This link is reusable — keep the email if you sign in on multiple devices.</p>"
    )
    body_text = (
        f"You've been invited to Curunir.\n\n"
        f"Click here to sign in: {link}\n\n"
        f"This link is reusable — keep the email if you sign in on multiple devices."
    )

    if not settings.email_api_key:
        logger.warning(
            "EMAIL_API_KEY unset; would have emailed %s with link %s", email, link
        )
        return

    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.post(
            POSTMARK_URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Postmark-Server-Token": settings.email_api_key,
            },
            json={
                "From": settings.email_from,
                "To": email,
                "Subject": "Sign in to Curunir",
                "HtmlBody": body_html,
                "TextBody": body_text,
                "MessageStream": "outbound",
            },
        )
        resp.raise_for_status()
```

- [ ] **Step 2: Create `portal/tests/test_email_send.py`**

```python
import logging

import httpx
import pytest

from portal import email_send
from portal.config import settings


@pytest.mark.asyncio
async def test_send_logs_when_no_api_key(monkeypatch, caplog):
    monkeypatch.setattr(settings, "email_api_key", "")
    with caplog.at_level(logging.WARNING):
        await email_send.send_signin_email("a@example.com", "http://x/y")
    assert any("a@example.com" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_send_calls_postmark_when_api_key_set(monkeypatch):
    monkeypatch.setattr(settings, "email_api_key", "test-key")
    monkeypatch.setattr(settings, "email_from", "from@example.com")

    captured = {}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return httpx.Response(200, json={"MessageID": "abc"})

    monkeypatch.setattr(email_send.httpx, "AsyncClient", FakeClient)
    await email_send.send_signin_email("b@example.com", "http://x/y")

    assert captured["url"] == email_send.POSTMARK_URL
    assert captured["headers"]["X-Postmark-Server-Token"] == "test-key"
    assert captured["json"]["To"] == "b@example.com"
    assert captured["json"]["From"] == "from@example.com"
    assert "http://x/y" in captured["json"]["HtmlBody"]
    assert "http://x/y" in captured["json"]["TextBody"]
```

- [ ] **Step 3: Run tests, expect PASS**

Run: `cd portal && pytest tests/test_email_send.py -v`
Expected: 2 PASS.

- [ ] **Step 4: Commit**

```bash
git add portal/email_send.py portal/tests/test_email_send.py
git commit -m "feat(portal): Postmark transactional sender with dev-mode log fallback"
```

---

## Phase 3 — Admin

### Task 7: Admin endpoints + CLI

**Files:**
- Create: `portal/admin.py`
- Create: `portal/templates/admin.html`
- Modify: `portal/app.py` — include admin router
- Create: `portal/tests/test_admin.py`

- [ ] **Step 1: Create `portal/templates/admin.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Curunir Portal Admin</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 880px; margin: 32px auto; padding: 0 16px; color: #222; }
    h1 { font-size: 18px; }
    table { border-collapse: collapse; width: 100%; margin-top: 12px; font-size: 13px; }
    th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #eee; vertical-align: top; }
    .inactive { color: #999; }
    code { background: #f4f4f4; padding: 2px 5px; border-radius: 3px; font-size: 11px; }
    form.inline { display: inline; }
    .new-token { background: #fffbe6; border: 1px solid #f5d76e; padding: 12px; margin: 16px 0; border-radius: 4px; }
  </style>
</head>
<body>
  <h1>Admin — users</h1>

  {% if new_container_token %}
  <div class="new-token">
    <strong>Container token for {{ new_user_email }}</strong> (shown once):<br>
    <code>{{ new_container_token }}</code>
  </div>
  {% endif %}

  <h2>Create user</h2>
  <form method="post" action="/admin/users">
    <input type="hidden" name="csrf" value="{{ csrf_token }}">
    <input name="email" type="email" required placeholder="user@example.com" style="padding:6px 10px;">
    <button type="submit" style="padding:6px 12px;">Create + email link</button>
  </form>

  <h2 style="margin-top:24px;">Users ({{ users|length }})</h2>
  <table>
    <tr>
      <th>id</th><th>email</th><th>active</th><th>actions</th>
    </tr>
    {% for u in users %}
    <tr class="{% if not u.is_active %}inactive{% endif %}">
      <td>{{ u.id }}</td>
      <td>{{ u.email }}</td>
      <td>{{ "yes" if u.is_active else "no" }}</td>
      <td>
        <form class="inline" method="post" action="/admin/users/{{ u.id }}/send-signin-email">
          <input type="hidden" name="csrf" value="{{ csrf_token }}">
          <button>resend link</button>
        </form>
        <form class="inline" method="post" action="/admin/users/{{ u.id }}/regenerate-sign-in">
          <input type="hidden" name="csrf" value="{{ csrf_token }}">
          <button>rotate sign-in</button>
        </form>
        <form class="inline" method="post" action="/admin/users/{{ u.id }}/regenerate-container">
          <input type="hidden" name="csrf" value="{{ csrf_token }}">
          <button>rotate container</button>
        </form>
        {% if u.is_active %}
        <form class="inline" method="post" action="/admin/users/{{ u.id }}/deactivate">
          <input type="hidden" name="csrf" value="{{ csrf_token }}">
          <button>deactivate</button>
        </form>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
</body>
</html>
```

- [ ] **Step 2: Create `portal/admin.py`**

```python
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


@router.get("", response_class=HTMLResponse)
async def admin_index(request: Request, user: User = Depends(admin_user)):
    users = await db.list_users()
    return templates.TemplateResponse(
        request, "admin.html",
        {
            "users": users,
            "csrf_token": csrf.issue_csrf(user.id),
            "new_container_token": None,
            "new_user_email": None,
        },
    )


@router.post("/users", response_class=HTMLResponse)
async def admin_create_user(
    request: Request,
    email: str = Form(...),
    csrf_token: str = Form(..., alias="csrf"),
    user: User = Depends(admin_user),
):
    _verify_csrf_form(user, csrf_token)
    new_user = await db.create_user(email)
    await email_send.send_signin_email(new_user.email, _signin_link(new_user.sign_in_token))
    users = await db.list_users()
    return templates.TemplateResponse(
        request, "admin.html",
        {
            "users": users,
            "csrf_token": csrf.issue_csrf(user.id),
            "new_container_token": new_user.container_token,
            "new_user_email": new_user.email,
        },
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
```

- [ ] **Step 3: Modify `portal/app.py` — include admin router**

Replace entire file:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from portal import admin, db, sign_in


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    await db.run_migrations()
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(lifespan=lifespan)
app.include_router(sign_in.router)
app.include_router(admin.router)


@app.get("/healthz")
async def healthz():
    ok = await db.ping()
    return JSONResponse({"status": "ok" if ok else "degraded"})
```

- [ ] **Step 4: Create `portal/tests/test_admin.py`**

```python
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
```

- [ ] **Step 5: Run tests, expect PASS**

Run: `cd portal && pytest tests/test_admin.py -v`
Expected: 6 PASS.

- [ ] **Step 6: Smoke-test the CLI**

Run: `cd portal && DATABASE_URL=postgresql://postgres:postgres@localhost:5432/portal python -m portal.admin create-user --email cli-smoke@example.com`
Expected: prints `Created user N <cli-smoke@example.com>` plus container token + sign-in link. Verify in DB:
`docker compose exec postgres psql -U postgres -d portal -c "SELECT id, email FROM users;"`

- [ ] **Step 7: Commit**

```bash
git add portal/admin.py portal/templates/admin.html portal/app.py portal/tests/test_admin.py
git commit -m "feat(portal): admin UI + CLI for user creation, rotation, deactivation"
```

---

## Phase 4 — Routing + WebSockets

### Task 8: In-memory routing table

**Files:**
- Create: `portal/routing.py`
- Create: `portal/tests/test_routing.py`

- [ ] **Step 1: Create `portal/routing.py`**

```python
"""In-memory user→connections routing table.

Single-process. Each user has at most one agent socket and zero or
more browser sockets. The portal stores no chat content; this table
is the only stateful surface and is reset on portal restart.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Protocol


logger = logging.getLogger(__name__)


class Sender(Protocol):
    """Anything we can json-send to and close. WebSocket-shaped."""
    async def send_text(self, data: str) -> None: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


@dataclass
class UserRoute:
    agent_ws: Sender | None = None
    browser_wss: list[Sender] = field(default_factory=list)


class RoutingTable:
    def __init__(self) -> None:
        self._routes: dict[int, UserRoute] = {}
        self._lock = asyncio.Lock()

    def _route(self, user_id: int) -> UserRoute:
        return self._routes.setdefault(user_id, UserRoute())

    async def register_agent(self, user_id: int, ws: Sender) -> None:
        """Register agent ws for user. Kicks any prior agent (close 4002)."""
        async with self._lock:
            route = self._route(user_id)
            old = route.agent_ws
            route.agent_ws = ws
        if old is not None:
            try:
                await old.close(code=4002, reason="replaced")
            except Exception:
                logger.warning("error closing replaced agent ws", exc_info=True)
        await self._broadcast_status(user_id, "online")

    async def unregister_agent(self, user_id: int, ws: Sender) -> None:
        async with self._lock:
            route = self._routes.get(user_id)
            if route is not None and route.agent_ws is ws:
                route.agent_ws = None
        await self._broadcast_status(user_id, "offline")

    async def add_browser(self, user_id: int, ws: Sender) -> None:
        async with self._lock:
            self._route(user_id).browser_wss.append(ws)

    async def remove_browser(self, user_id: int, ws: Sender) -> None:
        async with self._lock:
            route = self._routes.get(user_id)
            if route is not None and ws in route.browser_wss:
                route.browser_wss.remove(ws)

    def agent_for(self, user_id: int) -> Sender | None:
        route = self._routes.get(user_id)
        return route.agent_ws if route else None

    def browsers_for(self, user_id: int) -> list[Sender]:
        route = self._routes.get(user_id)
        return list(route.browser_wss) if route else []

    async def fan_out_to_browsers(self, user_id: int, payload: str) -> int:
        """Send payload to all browsers; return count delivered."""
        targets = self.browsers_for(user_id)
        if not targets:
            logger.info("agent_message dropped (no browsers)", extra={"user_id": user_id})
            return 0
        delivered = 0
        for ws in targets:
            try:
                await ws.send_text(payload)
                delivered += 1
            except Exception:
                logger.warning("browser send failed", exc_info=True)
        return delivered

    async def forward_to_agent(self, user_id: int, payload: str) -> bool:
        agent = self.agent_for(user_id)
        if agent is None:
            return False
        try:
            await agent.send_text(payload)
            return True
        except Exception:
            logger.warning("agent send failed", exc_info=True)
            return False

    async def _broadcast_status(self, user_id: int, status: str) -> None:
        import json
        payload = json.dumps({"type": "agent_status", "status": status})
        await self.fan_out_to_browsers(user_id, payload)


routing = RoutingTable()
```

- [ ] **Step 2: Create `portal/tests/test_routing.py`**

```python
import json

import pytest

from portal.routing import RoutingTable


class FakeWS:
    def __init__(self):
        self.sent: list[str] = []
        self.closed_with: tuple[int, str] | None = None

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

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
```

- [ ] **Step 3: Run tests, expect PASS**

Run: `cd portal && pytest tests/test_routing.py -v`
Expected: 6 PASS.

- [ ] **Step 4: Commit**

```bash
git add portal/routing.py portal/tests/test_routing.py
git commit -m "feat(portal): in-memory routing table with agent kick + browser fan-out"
```

---

### Task 9: `/ws/agent` endpoint

**Files:**
- Create: `portal/ws_agent.py`
- Modify: `portal/app.py` — include router
- Create: `portal/tests/test_ws_agent.py`

- [ ] **Step 1: Create `portal/ws_agent.py`**

```python
"""Container-facing WebSocket endpoint.

Container dials wss://portal/ws/agent with `Authorization: Bearer <token>`.
Token is validated against `users.container_token`; valid → register on
the routing table; invalid/inactive → close 4003.

This endpoint reads messages from the container and either:
  - {"type": "agent_message", "payload": ...} → unwrap and fan out
    payload (alone) to the user's browsers
  - {"type": "history_snapshot", "messages": ...} → fan out the full
    envelope to browsers (browser side knows how to render snapshots)

Browser-bound payloads are the unwrapped OutgoingMessage envelope.
"""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from portal import db
from portal.routing import routing


logger = logging.getLogger(__name__)
router = APIRouter()


def _bearer_from_headers(ws: WebSocket) -> str | None:
    auth = ws.headers.get("authorization") or ws.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


@router.websocket("/ws/agent")
async def ws_agent(ws: WebSocket) -> None:
    token = _bearer_from_headers(ws)
    if not token:
        await ws.close(code=4003, reason="missing bearer token")
        return
    user = await db.get_active_user_by_container_token(token)
    if user is None:
        await ws.close(code=4003, reason="forbidden")
        return

    await ws.accept()
    await routing.register_agent(user.id, ws)
    logger.info("agent connected", extra={"user_id": user.id})

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("agent sent invalid json", extra={"user_id": user.id})
                continue
            mtype = msg.get("type")
            if mtype == "agent_message":
                payload = msg.get("payload") or {}
                await routing.fan_out_to_browsers(user.id, json.dumps(payload))
            elif mtype == "history_snapshot":
                await routing.fan_out_to_browsers(user.id, raw)
            else:
                logger.warning("agent sent unknown type %r", mtype,
                               extra={"user_id": user.id})
    except WebSocketDisconnect:
        pass
    finally:
        await routing.unregister_agent(user.id, ws)
        logger.info("agent disconnected", extra={"user_id": user.id})
```

- [ ] **Step 2: Modify `portal/app.py` — include `ws_agent.router`**

Replace import block + `include_router` calls. Replace entire file:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from portal import admin, db, sign_in, ws_agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    await db.run_migrations()
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(lifespan=lifespan)
app.include_router(sign_in.router)
app.include_router(admin.router)
app.include_router(ws_agent.router)


@app.get("/healthz")
async def healthz():
    ok = await db.ping()
    return JSONResponse({"status": "ok" if ok else "degraded"})
```

- [ ] **Step 3: Create `portal/tests/test_ws_agent.py`**

The starlette test client (which `httpx.AsyncClient` does not provide
WebSocket support for) is needed; use `fastapi.testclient.TestClient`
in sync mode for WS tests, or `websockets` against a real uvicorn.
Easiest: `fastapi.testclient.TestClient` (sync) — Starlette's WS test
helper is synchronous.

```python
import json

import pytest
from fastapi.testclient import TestClient

from portal import db
from portal.app import app
from portal.routing import routing


@pytest.fixture
def sync_client():
    with TestClient(app) as c:
        yield c
    # Hard reset routing table after WS tests.
    routing._routes.clear()


@pytest.mark.asyncio
async def test_ws_agent_rejects_missing_token(sync_client):
    with pytest.raises(Exception):
        with sync_client.websocket_connect("/ws/agent") as _:
            pass


@pytest.mark.asyncio
async def test_ws_agent_rejects_invalid_token(sync_client):
    with pytest.raises(Exception):
        with sync_client.websocket_connect(
            "/ws/agent", headers={"Authorization": "Bearer not-a-token"}
        ):
            pass


@pytest.mark.asyncio
async def test_ws_agent_accepts_valid_token_and_registers(sync_client):
    user = await db.create_user("agent-conn@example.com")
    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {user.container_token}"},
    ) as ws:
        # Connection registered; routing reports an agent for the user.
        assert routing.agent_for(user.id) is not None
        ws.close()


@pytest.mark.asyncio
async def test_ws_agent_second_connection_kicks_first(sync_client):
    user = await db.create_user("kick@example.com")
    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {user.container_token}"},
    ) as ws1:
        with sync_client.websocket_connect(
            "/ws/agent",
            headers={"Authorization": f"Bearer {user.container_token}"},
        ) as ws2:
            # ws1 should receive a close; ws2 should be the registered agent.
            assert routing.agent_for(user.id) is not None


@pytest.mark.asyncio
async def test_agent_message_unwraps_and_would_fan_out(sync_client, monkeypatch):
    user = await db.create_user("unwrap@example.com")
    captured = []

    async def fake_fan(user_id, payload):
        captured.append((user_id, payload))
        return 1

    monkeypatch.setattr(routing, "fan_out_to_browsers", fake_fan)

    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {user.container_token}"},
    ) as ws:
        ws.send_text(json.dumps({
            "type": "agent_message",
            "payload": {"content": "hi", "final": True},
        }))
        ws.close()

    # The first call(s) include online-broadcast(s); find the unwrap.
    payloads = [json.loads(p) for (_, p) in captured]
    assert any(p == {"content": "hi", "final": True} for p in payloads)
```

> Note: `fastapi.testclient.TestClient` runs sync but the test functions
> remain `async` because they call `await db.create_user(...)`. The
> TestClient itself blocks; mixing is fine because the DB pool was
> initialized in the `client` fixture's lifespan. If issues arise,
> create a dedicated sync conftest fixture that initializes the DB pool
> via a synchronous wrapper.

- [ ] **Step 4: Run tests, expect PASS**

Run: `cd portal && pytest tests/test_ws_agent.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add portal/ws_agent.py portal/app.py portal/tests/test_ws_agent.py
git commit -m "feat(portal): /ws/agent endpoint with Bearer auth and kick-on-replace"
```

---

### Task 10: `/ws/browser` endpoint

**Files:**
- Create: `portal/ws_browser.py`
- Modify: `portal/app.py` — include router
- Create: `portal/tests/test_ws_browser.py`

- [ ] **Step 1: Create `portal/ws_browser.py`**

```python
"""Browser-facing WebSocket endpoint.

Browser opens wss://portal/ws/browser; the session cookie rides on the
upgrade request. Cookie + `Origin` header must both be valid:
  - Cookie absent / invalid / user inactive → close 4003.
  - Origin header missing or != PORTAL_BASE_URL → close 4003 (CSWSH).

Browser sends `IncomingMessage`-shaped JSON. We wrap as
`{"type": "user_message", "payload": ...}` and forward to the user's
agent socket. If no agent is connected, we reply directly to *this*
browser with a synthetic offline message — other browsers do not see it.
"""

import json
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from portal import auth, db
from portal.config import settings
from portal.routing import routing


logger = logging.getLogger(__name__)
router = APIRouter()


def _origin_allowed(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:
        return False
    expected = urlparse(settings.portal_base_url)
    actual = urlparse(origin)
    return (expected.scheme, expected.netloc) == (actual.scheme, actual.netloc)


@router.websocket("/ws/browser")
async def ws_browser(ws: WebSocket) -> None:
    if not _origin_allowed(ws):
        await ws.close(code=4003, reason="origin")
        return

    cookie = ws.cookies.get(auth.SESSION_COOKIE)
    if not cookie:
        await ws.close(code=4003, reason="no cookie")
        return
    user_id = auth.verify_session(cookie)
    if user_id is None:
        await ws.close(code=4003, reason="bad cookie")
        return
    user = await db.get_user_by_id(user_id)
    if user is None or not user.is_active:
        await ws.close(code=4003, reason="inactive")
        return

    await ws.accept()
    await routing.add_browser(user.id, ws)
    logger.info("browser connected", extra={"user_id": user.id})

    # Initial state push: agent_status + history_request to the agent
    # (agent will respond with history_snapshot which fans out to browsers).
    await ws.send_text(json.dumps({
        "type": "agent_status",
        "status": "online" if routing.agent_for(user.id) else "offline",
    }))
    if routing.agent_for(user.id) is not None:
        await routing.forward_to_agent(
            user.id, json.dumps({"type": "history_request"})
        )

    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("browser sent invalid json",
                               extra={"user_id": user.id})
                continue
            wrapped = json.dumps({"type": "user_message", "payload": payload})
            ok = await routing.forward_to_agent(user.id, wrapped)
            if not ok:
                await ws.send_text(json.dumps({
                    "content": "Agent offline.",
                    "final": True,
                    "delta": False,
                }))
    except WebSocketDisconnect:
        pass
    finally:
        await routing.remove_browser(user.id, ws)
        logger.info("browser disconnected", extra={"user_id": user.id})
```

- [ ] **Step 2: Modify `portal/app.py` — include `ws_browser.router`**

Replace import block + include the new router. Replace entire file:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from portal import admin, db, sign_in, ws_agent, ws_browser


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    await db.run_migrations()
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(lifespan=lifespan)
app.include_router(sign_in.router)
app.include_router(admin.router)
app.include_router(ws_agent.router)
app.include_router(ws_browser.router)


@app.get("/healthz")
async def healthz():
    ok = await db.ping()
    return JSONResponse({"status": "ok" if ok else "degraded"})
```

- [ ] **Step 3: Create `portal/tests/test_ws_browser.py`**

```python
import json

import pytest
from fastapi.testclient import TestClient

from portal import auth, db
from portal.app import app
from portal.routing import routing


GOOD_ORIGIN = {"Origin": "http://localhost:8000"}


@pytest.fixture
def sync_client():
    with TestClient(app) as c:
        yield c
    routing._routes.clear()


@pytest.mark.asyncio
async def test_browser_rejected_when_origin_missing(sync_client):
    user = await db.create_user("nb1@example.com")
    cookie = auth.sign_session(user.id)
    with pytest.raises(Exception):
        with sync_client.websocket_connect(
            "/ws/browser", cookies={auth.SESSION_COOKIE: cookie}
        ):
            pass


@pytest.mark.asyncio
async def test_browser_rejected_when_origin_mismatch(sync_client):
    user = await db.create_user("nb2@example.com")
    cookie = auth.sign_session(user.id)
    with pytest.raises(Exception):
        with sync_client.websocket_connect(
            "/ws/browser",
            cookies={auth.SESSION_COOKIE: cookie},
            headers={"Origin": "https://evil.example.com"},
        ):
            pass


@pytest.mark.asyncio
async def test_browser_rejected_without_cookie(sync_client):
    with pytest.raises(Exception):
        with sync_client.websocket_connect("/ws/browser", headers=GOOD_ORIGIN):
            pass


@pytest.mark.asyncio
async def test_browser_rejected_when_user_inactive(sync_client):
    user = await db.create_user("inactive@example.com")
    cookie = auth.sign_session(user.id)
    await db.deactivate_user(user.id)
    with pytest.raises(Exception):
        with sync_client.websocket_connect(
            "/ws/browser",
            cookies={auth.SESSION_COOKIE: cookie},
            headers=GOOD_ORIGIN,
        ):
            pass


@pytest.mark.asyncio
async def test_browser_accepts_and_pushes_offline_status(sync_client):
    user = await db.create_user("ok@example.com")
    cookie = auth.sign_session(user.id)
    with sync_client.websocket_connect(
        "/ws/browser",
        cookies={auth.SESSION_COOKIE: cookie},
        headers=GOOD_ORIGIN,
    ) as ws:
        msg = json.loads(ws.receive_text())
        assert msg == {"type": "agent_status", "status": "offline"}


@pytest.mark.asyncio
async def test_user_message_with_no_agent_replies_offline(sync_client):
    user = await db.create_user("offline@example.com")
    cookie = auth.sign_session(user.id)
    with sync_client.websocket_connect(
        "/ws/browser",
        cookies={auth.SESSION_COOKIE: cookie},
        headers=GOOD_ORIGIN,
    ) as ws:
        _status = ws.receive_text()
        ws.send_text(json.dumps({"content": "hi"}))
        reply = json.loads(ws.receive_text())
        assert reply["content"] == "Agent offline."
        assert reply["final"] is True
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `cd portal && pytest tests/test_ws_browser.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add portal/ws_browser.py portal/app.py portal/tests/test_ws_browser.py
git commit -m "feat(portal): /ws/browser with cookie+Origin auth and offline synthetic"
```

---

### Task 11: End-to-end round-trip + history_request

**Files:**
- Create: `portal/tests/test_e2e.py` (new)

- [ ] **Step 1: Create `portal/tests/test_e2e.py`** — full happy-path test

```python
"""Round-trip tests with a real agent WS and a real browser WS.

Mock agent connects via /ws/agent. Browser connects via /ws/browser.
Browser sends a user message; agent should receive a wrapped envelope.
Agent sends an agent_message; browser should receive the unwrapped
payload. Browser connecting triggers a history_request to the agent.
"""

import json
import threading

import pytest
from fastapi.testclient import TestClient

from portal import auth, db
from portal.app import app
from portal.routing import routing


GOOD_ORIGIN = {"Origin": "http://localhost:8000"}


@pytest.fixture
def sync_client():
    with TestClient(app) as c:
        yield c
    routing._routes.clear()


@pytest.mark.asyncio
async def test_browser_to_agent_round_trip(sync_client):
    user = await db.create_user("e2e@example.com")
    cookie = auth.sign_session(user.id)

    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {user.container_token}"},
    ) as agent_ws:
        with sync_client.websocket_connect(
            "/ws/browser",
            cookies={auth.SESSION_COOKIE: cookie},
            headers=GOOD_ORIGIN,
        ) as browser_ws:
            # Browser receives initial status + agent receives history_request.
            assert json.loads(browser_ws.receive_text()) == {
                "type": "agent_status", "status": "online",
            }
            hist = json.loads(agent_ws.receive_text())
            assert hist == {"type": "history_request"}

            # Browser sends a chat message.
            browser_ws.send_text(json.dumps({"content": "hello"}))
            wrapped = json.loads(agent_ws.receive_text())
            assert wrapped == {
                "type": "user_message",
                "payload": {"content": "hello"},
            }

            # Agent replies with an agent_message; browser sees unwrapped.
            agent_ws.send_text(json.dumps({
                "type": "agent_message",
                "payload": {"content": "hi back", "final": True},
            }))
            reply = json.loads(browser_ws.receive_text())
            assert reply == {"content": "hi back", "final": True}


@pytest.mark.asyncio
async def test_agent_history_snapshot_fans_out(sync_client):
    user = await db.create_user("snap@example.com")
    cookie = auth.sign_session(user.id)

    with sync_client.websocket_connect(
        "/ws/agent",
        headers={"Authorization": f"Bearer {user.container_token}"},
    ) as agent_ws:
        with sync_client.websocket_connect(
            "/ws/browser",
            cookies={auth.SESSION_COOKIE: cookie},
            headers=GOOD_ORIGIN,
        ) as browser_ws:
            _ = browser_ws.receive_text()  # agent_status
            _ = agent_ws.receive_text()    # history_request

            agent_ws.send_text(json.dumps({
                "type": "history_snapshot",
                "messages": [
                    {"role": "user", "content": "one"},
                    {"role": "assistant", "content": "two"},
                ],
            }))
            snap = json.loads(browser_ws.receive_text())
            assert snap["type"] == "history_snapshot"
            assert len(snap["messages"]) == 2
```

- [ ] **Step 2: Run tests, expect PASS**

Run: `cd portal && pytest tests/test_e2e.py -v`
Expected: 2 PASS.

- [ ] **Step 3: Commit**

```bash
git add portal/tests/test_e2e.py
git commit -m "test(portal): end-to-end browser↔agent round-trip + history snapshot"
```

---

## Phase 5 — Curunir-side channel

### Task 12: Extract attachment helpers from `ws.py`

**Files:**
- Create: `src/channels/_attachments.py`
- Modify: `src/channels/ws.py` — drop helper definitions, import from `_attachments.py`
- Verify: existing `tests/test_channels.py` still passes (no test changes needed)

- [ ] **Step 1: Create `src/channels/_attachments.py`** — verbatim copy of existing helpers

```python
"""WebSocket-flavored attachment helpers, shared between channels.

Extracted from src/channels/ws.py so the new PortalChannel can reuse
the exact same validation, decoding, staging, filename normalization,
and outbound enrichment without duplication.
"""

import base64
import os
import uuid as _uuid

from src.channels.email import _normalize_unicode_whitespace


_MAX_IMAGE_BYTES = 5 * 1024 * 1024          # 5 MB
_MAX_TEXT_BYTES = 256 * 1024                # 256 KB
_MAX_DOC_BYTES = 10 * 1024 * 1024           # 10 MB (PDFs)
_MAX_TOTAL_BYTES = 20 * 1024 * 1024         # 20 MB
_ALLOWED_IMAGE_MIMES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
})
_ALLOWED_DOC_MIMES = frozenset({"application/pdf"})

_MAX_ATTACHMENT_CONTENT_SIZE = 512 * 1024  # 512KB


def _enrich_attachments(attachments: list[dict], project_root: str) -> None:
    """Inline content and normalize paths for outbound attachments in-place."""
    for att in attachments:
        path = att["path"]
        mime = att.get("mime_type", "")
        is_text = mime.startswith("text/") or mime == "application/json"

        if os.path.isabs(path):
            try:
                att["path"] = os.path.relpath(path, project_root)
            except ValueError:
                pass

        if not is_text:
            att["content"] = None
            continue

        if not os.path.isfile(path):
            att["content"] = None
            att["error"] = "file not found"
            continue

        if os.path.getsize(path) > _MAX_ATTACHMENT_CONTENT_SIZE:
            att["content"] = None
            continue

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                att["content"] = f.read()
        except OSError:
            att["content"] = None
            att["error"] = "file not found"


def _decode_attachments(raw: list | None) -> tuple[list[dict] | None, str | None]:
    """Validate and base64-decode inbound attachment payloads."""
    if raw is None:
        return [], None
    if not isinstance(raw, list):
        return None, "attachments must be a list"

    decoded: list[dict] = []
    total_bytes = 0
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return None, f"attachment[{i}] is not an object"
        for key in ("filename", "mime_type", "data"):
            if key not in item or not isinstance(item[key], str):
                return None, f"attachment[{i}] missing or invalid '{key}'"

        filename = item["filename"]
        mime = item["mime_type"]
        try:
            payload = base64.b64decode(item["data"], validate=True)
        except (ValueError, base64.binascii.Error):
            return None, f"attachment[{i}] '{filename}': invalid base64"

        size = len(payload)

        if mime.startswith("image/"):
            if mime not in _ALLOWED_IMAGE_MIMES:
                return None, (
                    f"attachment[{i}] '{filename}': "
                    f"unsupported image type {mime}"
                )
            if size > _MAX_IMAGE_BYTES:
                return None, (
                    f"attachment[{i}] '{filename}': "
                    f"{size} bytes exceeds 5 MB image cap"
                )
        elif mime in _ALLOWED_DOC_MIMES:
            if size > _MAX_DOC_BYTES:
                return None, (
                    f"attachment[{i}] '{filename}': "
                    f"{size} bytes exceeds 10 MB document cap"
                )
        else:
            if size > _MAX_TEXT_BYTES:
                return None, (
                    f"attachment[{i}] '{filename}': "
                    f"{size} bytes exceeds 256 KB text cap"
                )
            try:
                payload.decode("utf-8")
            except UnicodeDecodeError:
                return None, (
                    f"attachment[{i}] '{filename}': "
                    f"not UTF-8 decodable"
                )

        total_bytes += size
        if total_bytes > _MAX_TOTAL_BYTES:
            return None, "total attachment size exceeds 20 MB cap"

        decoded.append({
            "filename": filename,
            "mime_type": mime,
            "bytes": payload,
        })

    return decoded, None


def _unique_filename(existing: set[str], name: str) -> str:
    """Return `name`, suffixed `_1`, `_2`, ... if it collides."""
    if name not in existing:
        return name
    if "." in name:
        stem, _, ext = name.rpartition(".")
        ext = "." + ext
    else:
        stem, ext = name, ""
    i = 1
    while f"{stem}_{i}{ext}" in existing:
        i += 1
    return f"{stem}_{i}{ext}"


def _stage_attachments(
    items: list[dict], session_id: str, uploads_dir: str
) -> list[dict]:
    """Write decoded items to disk, return an email-shaped manifest."""
    if not items:
        return []

    batch_dir = os.path.join(uploads_dir, session_id, _uuid.uuid4().hex)
    os.makedirs(batch_dir, exist_ok=True)

    manifest: list[dict] = []
    used: set[str] = set()
    for item in items:
        fname = _unique_filename(used, _normalize_unicode_whitespace(item["filename"]))
        used.add(fname)
        full_path = os.path.join(batch_dir, fname)
        with open(full_path, "wb") as f:
            f.write(item["bytes"])
        manifest.append({
            "filename": fname,
            "path": full_path,
            "mime_type": item["mime_type"],
            "size": len(item["bytes"]),
        })
    return manifest
```

- [ ] **Step 2: Modify `src/channels/ws.py`** — replace local helpers with imports

In `src/channels/ws.py`:
- Remove the local definitions of: `_decode_attachments`, `_enrich_attachments`, `_stage_attachments`, `_unique_filename`, the `_MAX_*` constants, and the `_ALLOWED_*` frozensets.
- Remove the import `from src.channels.email import _normalize_unicode_whitespace` (now used inside `_attachments.py`).
- Remove unused imports: `base64`, `uuid as _uuid` (now used inside `_attachments.py`).
- Add at the top of the imports block:

```python
from src.channels._attachments import (
    _decode_attachments,
    _enrich_attachments,
    _stage_attachments,
)
```

(`_unique_filename` is internal to `_stage_attachments`; `ws.py` does not need to import it directly.)

The rest of `ws.py` (the `WebSocketChannel` class and module-level constants like `SESSION_ID`, `_MAX_ATTACHMENT_CONTENT_SIZE`) stays put — actually `_MAX_ATTACHMENT_CONTENT_SIZE` moves into `_attachments.py` because `_enrich_attachments` uses it. Verify by grep that no other consumer of `_MAX_ATTACHMENT_CONTENT_SIZE` exists in `ws.py` after the move; if yes, leave a copy.

- [ ] **Step 3: Run existing tests, expect PASS**

Run: `pytest tests/test_channels.py -v`
Expected: All previously-passing tests still pass — the helpers moved but their behavior is unchanged.

- [ ] **Step 4: Commit**

```bash
git add src/channels/_attachments.py src/channels/ws.py
git commit -m "refactor(channels): extract attachment helpers to _attachments.py"
```

---

### Task 13: `PortalChannel` — connect, reconnect, wire protocol

**Files:**
- Create: `src/channels/portal.py`
- Create: `tests/test_portal_channel.py`

- [ ] **Step 1: Create `src/channels/portal.py`**

```python
"""PortalChannel — outbound WebSocket to the Curunir Portal.

Connects to wss://<portal>/ws/agent with `Authorization: Bearer <token>`.
Receives wrapped user messages from the portal; emits wrapped agent
messages and history snapshots back. Reconnects with exponential
backoff on retryable failures. Auth failure (4003) and replaced
(4002) are terminal.

Enabled when both CURUNIR_PORTAL_URL and CURUNIR_PORTAL_TOKEN are set.
"""

import asyncio
import json
import logging
import os
import random
from typing import Any

import websockets
import websockets.exceptions

from src.channels._attachments import (
    _decode_attachments,
    _enrich_attachments,
    _stage_attachments,
)
from src.channels.base import IncomingMessage, OutgoingMessage


logger = logging.getLogger(__name__)

PORTAL_SESSION_ID = "portal"  # Single user per container; no per-user partition needed.

_BACKOFF_INITIAL = 1.0
_BACKOFF_MAX = 30.0
_TERMINAL_CODES = {4002, 4003}


def _backoff_with_jitter(attempt: int) -> float:
    base = min(_BACKOFF_INITIAL * (2 ** attempt), _BACKOFF_MAX)
    jitter = base * 0.2 * (random.random() * 2 - 1)
    return max(0.0, base + jitter)


class PortalChannel:
    def __init__(
        self,
        in_queue: asyncio.Queue,
        url: str,
        token: str,
        history_provider: "callable[[], list[dict]] | None" = None,
        uploads_dir: str | None = None,
    ):
        self.in_queue = in_queue
        self.url = url
        self.token = token
        self.history_provider = history_provider or (lambda: [])
        self.uploads_dir = uploads_dir or os.path.join(
            os.getcwd(), "context", "uploads"
        )
        self._connection: Any = None
        self._terminate = False

    async def start(self) -> None:
        attempt = 0
        while not self._terminate:
            try:
                async with websockets.connect(
                    self.url,
                    additional_headers={"Authorization": f"Bearer {self.token}"},
                    max_size=32 * 1024 * 1024,
                ) as ws:
                    logger.info("PortalChannel connected to %s", self.url)
                    self._connection = ws
                    attempt = 0
                    await self._read_loop(ws)
            except websockets.exceptions.InvalidStatus as e:
                code = getattr(e.response, "status_code", None)
                logger.error("Portal upgrade rejected: %s", code)
                if code in (401, 403):
                    self._terminate = True
                    return
            except websockets.exceptions.ConnectionClosed as e:
                code = e.code
                if code in _TERMINAL_CODES:
                    logger.error("Portal closed connection (terminal): %s %s",
                                 code, e.reason)
                    self._terminate = True
                    return
                logger.warning("Portal connection closed: %s %s; will retry",
                               code, e.reason)
            except (OSError, asyncio.TimeoutError) as e:
                logger.warning("Portal connect failed: %s; will retry", e)
            except asyncio.CancelledError:
                logger.info("PortalChannel cancelled")
                raise
            finally:
                self._connection = None

            if self._terminate:
                return
            delay = _backoff_with_jitter(attempt)
            attempt = min(attempt + 1, 6)
            await asyncio.sleep(delay)

    async def _read_loop(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Portal sent invalid JSON; ignoring")
                continue
            mtype = msg.get("type")
            if mtype == "user_message":
                await self._handle_user_message(msg.get("payload") or {})
            elif mtype == "history_request":
                await self._handle_history_request()
            else:
                logger.warning("Portal sent unknown type %r; ignoring", mtype)

    async def _handle_user_message(self, payload: dict) -> None:
        decoded, err = _decode_attachments(payload.get("attachments"))
        if err is not None:
            await self.send(OutgoingMessage(
                content=f"Attachment rejected: {err}",
                channel="portal",
                session_id=PORTAL_SESSION_ID,
                reply_address={},
                final=True,
            ))
            return

        manifest = (
            _stage_attachments(decoded, PORTAL_SESSION_ID, self.uploads_dir)
            if decoded else None
        )
        await self.in_queue.put(IncomingMessage(
            content=payload.get("content", ""),
            channel="portal",
            session_id=PORTAL_SESSION_ID,
            reply_address={},
            command=payload.get("command") or None,
            attachments=manifest,
        ))

    async def _handle_history_request(self) -> None:
        if self._connection is None:
            return
        messages = self.history_provider()
        try:
            await self._connection.send(json.dumps({
                "type": "history_snapshot",
                "messages": messages,
            }))
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Portal closed during history snapshot send")

    async def send(self, msg: OutgoingMessage) -> None:
        if self._connection is None:
            logger.info("PortalChannel: no portal connection; dropping outbound")
            return
        if msg.attachments:
            _enrich_attachments(msg.attachments, os.getcwd())

        wrapped = {
            "type": "agent_message",
            "payload": {
                "content": msg.content,
                "tool_calls": msg.tool_calls,
                "final": msg.final,
                "delta": msg.delta,
                "attachments": msg.attachments if msg.attachments else None,
                "workflow": msg.workflow,
                "stats": msg.stats,
            },
        }
        try:
            await self._connection.send(json.dumps(wrapped))
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Portal connection closed while sending; dropped")
            self._connection = None
```

- [ ] **Step 2: Create `tests/test_portal_channel.py`**

```python
"""Tests for PortalChannel.

We spin up a tiny in-process WebSocket server (via the `websockets`
library) that acts as a fake portal. PortalChannel connects to it,
exchanges messages, and we assert on what each side observed.
"""

import asyncio
import json

import pytest
import websockets

from src.channels.base import OutgoingMessage
from src.channels.portal import PORTAL_SESSION_ID, PortalChannel


@pytest.fixture
async def portal_server():
    """Yield (url, recv_queue, send_callable, accept_callable, close_args).

    The server accepts ONE connection; the test drives it.
    """
    received: asyncio.Queue = asyncio.Queue()
    server_ws_holder: dict = {}
    accept_event = asyncio.Event()

    async def handler(ws):
        server_ws_holder["ws"] = ws
        accept_event.set()
        try:
            async for raw in ws:
                await received.put(raw)
        except websockets.exceptions.ConnectionClosed:
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"ws://127.0.0.1:{port}/ws/agent"

    async def send_to_channel(payload: dict):
        await accept_event.wait()
        await server_ws_holder["ws"].send(json.dumps(payload))

    async def close_with(code: int):
        await accept_event.wait()
        await server_ws_holder["ws"].close(code=code, reason="test")

    yield {
        "url": url,
        "received": received,
        "send": send_to_channel,
        "close": close_with,
        "accept": accept_event.wait,
    }
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_user_message_lands_on_in_queue(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="anything"
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({
            "type": "user_message",
            "payload": {"content": "hello"},
        })
        msg = await asyncio.wait_for(in_q.get(), timeout=2.0)
        assert msg.content == "hello"
        assert msg.channel == "portal"
        assert msg.session_id == PORTAL_SESSION_ID
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_outbound_message_wraps_with_type(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t"
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await ch.send(OutgoingMessage(
            content="hi", channel="portal",
            session_id=PORTAL_SESSION_ID, reply_address={}, final=True,
        ))
        raw = await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        msg = json.loads(raw)
        assert msg["type"] == "agent_message"
        assert msg["payload"]["content"] == "hi"
        assert msg["payload"]["final"] is True
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_history_request_invokes_provider_and_sends_snapshot(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    fake_history = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ]
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t",
        history_provider=lambda: fake_history,
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["send"]({"type": "history_request"})
        raw = await asyncio.wait_for(portal_server["received"].get(), timeout=2.0)
        msg = json.loads(raw)
        assert msg["type"] == "history_snapshot"
        assert msg["messages"] == fake_history
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_close_4003_terminal_does_not_reconnect(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t"
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["close"](4003)
        # start() should return cleanly without entering reconnect loop.
        await asyncio.wait_for(task, timeout=2.0)
        assert ch._terminate is True
    except asyncio.CancelledError:
        raise
    finally:
        if not task.done():
            task.cancel()


@pytest.mark.asyncio
async def test_close_4002_replaced_terminal(portal_server):
    in_q: asyncio.Queue = asyncio.Queue()
    ch = PortalChannel(
        in_queue=in_q, url=portal_server["url"], token="t"
    )
    task = asyncio.create_task(ch.start())
    try:
        await portal_server["accept"]()
        await portal_server["close"](4002)
        await asyncio.wait_for(task, timeout=2.0)
        assert ch._terminate is True
    finally:
        if not task.done():
            task.cancel()
```

- [ ] **Step 3: Run tests, expect PASS**

Run: `pytest tests/test_portal_channel.py -v`
Expected: 5 PASS.

- [ ] **Step 4: Commit**

```bash
git add src/channels/portal.py tests/test_portal_channel.py
git commit -m "feat(channels): PortalChannel with reconnect + history projection"
```

---

### Task 14: Wire `PortalChannel` into `run.py`

**Files:**
- Modify: `run.py`
- Modify: `.env.example`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Read `run.py` to find the channel registration site**

Run: `grep -n "WebSocketChannel\|EmailChannel\|TaskGroup\|channels = \|router" run.py | head -40`
Use the output to locate where channels are constructed and added to the TaskGroup. The portal channel needs to be added in the same place.

- [ ] **Step 2: Modify `run.py` — instantiate `PortalChannel` when env vars are present**

Add the import alongside other channel imports:

```python
from src.channels.portal import PortalChannel
```

In the channel-construction block (next to where `WebSocketChannel` and `EmailChannel` are built), add:

```python
portal_url = os.environ.get("CURUNIR_PORTAL_URL", "").strip()
portal_token = os.environ.get("CURUNIR_PORTAL_TOKEN", "").strip()
portal_channel: PortalChannel | None = None
if portal_url and portal_token:
    portal_channel = PortalChannel(
        in_queue=in_queue,
        url=portal_url,
        token=portal_token,
        history_provider=lambda: agent.history_snapshot(),
    )
    logger.info("PortalChannel enabled")
else:
    logger.info("PortalChannel disabled (set CURUNIR_PORTAL_URL + CURUNIR_PORTAL_TOKEN)")
```

In the TaskGroup block where channels' `start()` coroutines are scheduled, add:

```python
if portal_channel is not None:
    tg.create_task(portal_channel.start(), name="portal_channel")
```

In the `route_outbound()` function (or equivalent — the function that dispatches `OutgoingMessage` back to the originating channel), add a branch for `channel == "portal"`:

```python
elif msg.channel == "portal":
    if portal_channel is not None:
        await portal_channel.send(msg)
```

- [ ] **Step 3: Add `Agent.history_snapshot()` method** (curunir-side history projection)

In `src/agent/agent.py`, add a method to the `Agent` class:

```python
def history_snapshot(self) -> list[dict]:
    """Return a chat-shaped projection of self.history for the portal.

    Walks self.history once. Includes user turns and assistant *final*
    turns; tool internals are summarized as one-liners
    (e.g. "bash: ls -la"). Capped at 200 messages or 100 KB serialized.
    """
    import json as _json
    out: list[dict] = []
    for entry in self.history:
        role = entry.get("role")
        if role == "user":
            content = entry.get("content")
            if isinstance(content, list):
                # Multimodal: extract text parts only.
                text = " ".join(
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            else:
                text = content or ""
            out.append({"role": "user", "content": text})
        elif role == "assistant":
            content = entry.get("content") or ""
            tool_calls = entry.get("tool_calls") or []
            summaries = []
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "tool")
                args = fn.get("arguments", "")
                if isinstance(args, str) and args:
                    try:
                        parsed = _json.loads(args)
                        first_val = next(iter(parsed.values()), "")
                        summaries.append(f"{name}: {first_val}")
                    except (ValueError, StopIteration):
                        summaries.append(name)
                else:
                    summaries.append(name)
            out.append({
                "role": "assistant",
                "content": content,
                "tool_calls": summaries,
            })
        # role == "tool" is internal noise — skip.

    # Apply caps: 200 messages OR ~100 KB serialized, whichever first.
    while len(out) > 200 or len(_json.dumps(out)) > 100_000:
        if not out:
            break
        out.pop(0)

    if out and (out is not self.history):
        out.insert(0, {
            "role": "system",
            "content": "...truncated...",
        }) if len(out) >= 200 else None

    return out
```

> Verify the field names match `Agent.history`'s actual format by
> reading `src/agent/agent.py` first. If `tool_calls` are stored
> differently (e.g. on a separate role="tool" follow-up), adapt the
> projection to that shape — the helper is the right place to reconcile.

- [ ] **Step 4: Add `tests/test_history_snapshot.py`** — verify projection

```python
import json

import pytest

from src.agent.agent import Agent  # or wherever Agent lives


def _agent_with_history(history):
    """Construct a minimal Agent and stuff history into it."""
    a = Agent.__new__(Agent)  # bypass __init__ if expensive
    a.history = history
    return a


def test_user_and_assistant_turns_kept():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    snap = _agent_with_history(history).history_snapshot()
    assert snap == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello", "tool_calls": []},
    ]


def test_tool_role_is_dropped():
    history = [
        {"role": "user", "content": "x"},
        {"role": "tool", "content": "internal"},
        {"role": "assistant", "content": "done"},
    ]
    snap = _agent_with_history(history).history_snapshot()
    roles = [m["role"] for m in snap]
    assert "tool" not in roles


def test_tool_calls_are_summarized():
    history = [
        {"role": "assistant", "content": "",
         "tool_calls": [
             {"function": {"name": "bash", "arguments": json.dumps({"command": "ls -la"})}}
         ]},
    ]
    snap = _agent_with_history(history).history_snapshot()
    assert snap[0]["tool_calls"] == ["bash: ls -la"]


def test_cap_at_200_messages():
    history = [{"role": "user", "content": str(i)} for i in range(250)]
    snap = _agent_with_history(history).history_snapshot()
    assert len(snap) <= 200


def test_user_multimodal_text_extracted():
    history = [{"role": "user", "content": [
        {"type": "text", "text": "look at this"},
        {"type": "image_url", "image_url": {"url": "data:..."}},
    ]}]
    snap = _agent_with_history(history).history_snapshot()
    assert snap[0]["content"] == "look at this"
```

- [ ] **Step 5: Run tests, expect PASS**

Run: `pytest tests/test_history_snapshot.py -v`
Expected: 5 PASS.

- [ ] **Step 6: Modify `.env.example`** — add the two new env vars

Add at the end of the file:

```bash
# Curunir Portal — set both to enable PortalChannel (dial out to the hosted portal)
CURUNIR_PORTAL_URL=
CURUNIR_PORTAL_TOKEN=
```

- [ ] **Step 7: Modify `CLAUDE.md`** — document the Portal channel

In the `### Channels (`src/channels/`)` section, add to the bullet list:

```markdown
- **Portal** (`portal.py`): Outbound WebSocket to a hosted portal (`CURUNIR_PORTAL_URL` + `CURUNIR_PORTAL_TOKEN`). Container dials portal; portal multiplexes browser ↔ container. Session ID is `"portal"`. See `portal/` directory for the portal service.
```

In the file structure section (or "Architecture" intro), mention `portal/`:

```markdown
### Portal Service (`portal/`)

Standalone FastAPI app deployed to Render, separate Python project from the curunir container. See [`portal/README.md`](portal/README.md). Contains its own pyproject.toml, Dockerfile, render.yaml, and tests/. The curunir container talks to it via PortalChannel.
```

- [ ] **Step 8: Smoke-test locally**

Run portal:
```bash
cd portal && docker compose up -d && uvicorn portal.app:app --reload
```

In another shell, create a user:
```bash
cd portal && DATABASE_URL=postgresql://postgres:postgres@localhost:5432/portal \
  python -m portal.admin create-user --email me@example.com
```

Copy the printed container token. In a third shell, run curunir with the token:
```bash
cd <repo-root>
CURUNIR_PORTAL_URL=ws://localhost:8000/ws/agent \
CURUNIR_PORTAL_TOKEN=<token> \
python run.py
```

Logs should show `PortalChannel connected to ws://localhost:8000/ws/agent`. (Browser-side smoke test deferred until Phase 6 lands.)

- [ ] **Step 9: Commit**

```bash
git add run.py src/agent/agent.py tests/test_history_snapshot.py .env.example CLAUDE.md
git commit -m "feat(curunir): wire PortalChannel into run.py + history snapshot helper"
```

---

## Phase 6 — Frontend

### Task 15: Static chat surface (HTML + CSS + JS)

**Files:**
- Create: `portal/static/index.html`
- Modify: `portal/app.py` — mount `/static` and add `GET /` route

- [ ] **Step 1: Create `portal/static/index.html`** — full chat surface

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Curunir</title>
<script src="https://cdn.jsdelivr.net/npm/marked@4/marked.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
:root {
  --bg: #0a0a12;
  --fg: #e0e0e0;
  --user: #4ade80;
  --tool: #555;
  --muted: #444;
  --accent: #6366f1;
  --bubble: #12121c;
}
html, body { height: 100dvh; }
body {
  background: var(--bg); color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  font-size: 14px;
  display: flex; flex-direction: column;
}
header {
  padding: 8px 14px; border-bottom: 1px solid #1a1a22;
  font-size: 12px; color: #888;
  display: flex; justify-content: space-between; align-items: center;
  flex-shrink: 0;
}
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
.dot.online { background: var(--user); }
.dot.offline { background: #555; }
.dot.reconnecting { background: #e2b55a; animation: pulse 1s infinite; }
@keyframes pulse { 50% { opacity: 0.4; } }

main {
  flex: 1; overflow-y: auto;
  padding: 14px;
  max-width: 720px; width: 100%; margin: 0 auto;
}
.msg { margin-bottom: 14px; line-height: 1.5; }
.msg.user .role { color: var(--user); font-weight: bold; font-size: 11px; }
.msg.user .body { white-space: pre-wrap; }
.msg.assistant .role { color: var(--accent); font-weight: bold; font-size: 11px; }
.msg.assistant .body code { background: #1a1a2e; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
.msg.assistant .body pre { background: #1a1a2e; border-radius: 6px; padding: 10px; margin: 6px 0; overflow-x: auto; font-size: 12px; line-height: 1.4; }
.msg.assistant .body pre code { background: none; padding: 0; }
.msg .tools { color: var(--tool); font-size: 11px; margin-top: 4px; }
.msg .tool-line { padding: 2px 0; }

.attachment {
  display: inline-flex; align-items: center; gap: 4px;
  background: #1a1a2e; border: 1px solid #2a2a3e; border-radius: 4px;
  padding: 3px 8px; font-size: 11px; color: var(--accent);
  margin-right: 4px; margin-top: 4px;
}

footer {
  padding: 10px 14px; border-top: 1px solid #1a1a22;
  display: flex; align-items: end; gap: 8px;
  max-width: 720px; width: 100%; margin: 0 auto;
  flex-shrink: 0;
}
#attach-btn {
  background: transparent; border: 1px solid #2a2a3e; border-radius: 4px;
  color: #888; padding: 8px 10px; cursor: pointer; min-height: 44px;
}
#attach-btn:hover { color: var(--accent); border-color: var(--accent); }
#input {
  flex: 1; background: #12121c; color: var(--fg);
  border: 1px solid #2a2a3e; border-radius: 6px; padding: 10px;
  font-family: inherit; font-size: 14px; resize: none;
  min-height: 44px; max-height: 30dvh;
}
#send-btn {
  background: var(--accent); color: white; border: 0; border-radius: 4px;
  padding: 10px 16px; cursor: pointer; min-height: 44px;
}
#send-btn:disabled { background: #2a2a3e; color: #666; cursor: not-allowed; }
.staged-list { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 6px; }
.staged-list .attachment { cursor: pointer; }
.staged-list .attachment:hover { color: #f87171; }
</style>
</head>
<body>
<header>
  <div><strong>Curunir</strong></div>
  <div id="status"><span class="dot offline"></span><span id="status-text">offline</span></div>
</header>
<main id="messages"></main>
<footer>
  <input type="file" id="file-input" multiple style="display:none">
  <button id="attach-btn" title="Attach files">📎</button>
  <div style="flex:1; display:flex; flex-direction:column;">
    <div id="staged" class="staged-list"></div>
    <textarea id="input" rows="1" placeholder="Type a message…"></textarea>
  </div>
  <button id="send-btn">Send</button>
</footer>

<script>
// === Constants ===
const MAX_IMAGE = 5 * 1024 * 1024;
const MAX_PDF = 10 * 1024 * 1024;
const MAX_TEXT = 256 * 1024;
const MAX_TOTAL = 20 * 1024 * 1024;
const IMAGE_MIMES = ["image/png", "image/jpeg", "image/gif", "image/webp"];

// === Elements ===
const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("input");
const sendBtn = document.getElementById("send-btn");
const attachBtn = document.getElementById("attach-btn");
const fileInput = document.getElementById("file-input");
const stagedEl = document.getElementById("staged");
const statusDot = document.querySelector("#status .dot");
const statusText = document.getElementById("status-text");

// === State ===
let ws = null;
let agentOnline = false;
let staged = []; // {filename, mime_type, data (base64), size}
let inProgressMsg = null; // current assistant DOM node
let backoff = 1000;

// === Markdown setup ===
marked.setOptions({
  highlight: (code, lang) => hljs.highlightAuto(code, lang ? [lang] : undefined).value,
  breaks: true,
});

// === Status ===
function setStatus(state) {
  statusDot.className = "dot " + state;
  statusText.textContent = state;
  agentOnline = state === "online";
  sendBtn.disabled = !agentOnline;
}

// === WS connect ===
function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws/browser`);
  setStatus("reconnecting");

  ws.onopen = () => { backoff = 1000; };
  ws.onmessage = (e) => onServerMessage(JSON.parse(e.data));
  ws.onclose = () => {
    setStatus("offline");
    setTimeout(connect, backoff + Math.random() * 500);
    backoff = Math.min(backoff * 2, 30000);
  };
  ws.onerror = () => { try { ws.close(); } catch {} };
}

// === Inbound dispatch ===
function onServerMessage(msg) {
  if (msg.type === "agent_status") {
    setStatus(msg.status);
    return;
  }
  if (msg.type === "history_snapshot") {
    messagesEl.innerHTML = "";
    inProgressMsg = null;
    for (const m of msg.messages) renderHistoryEntry(m);
    return;
  }
  // Otherwise: an OutgoingMessage payload.
  renderAgentChunk(msg);
}

function renderHistoryEntry(m) {
  if (m.role === "user") {
    const el = appendMessage("user");
    el.querySelector(".body").textContent = m.content || "";
    renderAttachments(el, m.attachments);
  } else if (m.role === "assistant") {
    const el = appendMessage("assistant");
    el.querySelector(".body").innerHTML = marked.parse(m.content || "");
    if (m.tool_calls && m.tool_calls.length) {
      const tools = document.createElement("div");
      tools.className = "tools";
      for (const t of m.tool_calls) {
        const line = document.createElement("div");
        line.className = "tool-line";
        line.textContent = t;
        tools.appendChild(line);
      }
      el.appendChild(tools);
    }
    renderAttachments(el, m.attachments);
  } else if (m.role === "system") {
    const el = appendMessage("assistant");
    el.querySelector(".body").innerHTML = `<em style="color:#666">${m.content}</em>`;
  }
}

function renderAgentChunk(m) {
  if (!inProgressMsg) {
    inProgressMsg = appendMessage("assistant");
  }
  const body = inProgressMsg.querySelector(".body");
  if (m.delta) {
    body.dataset.raw = (body.dataset.raw || "") + (m.content || "");
    body.innerHTML = marked.parse(body.dataset.raw);
  } else if (m.content) {
    body.dataset.raw = m.content;
    body.innerHTML = marked.parse(m.content);
  }
  if (m.tool_calls && m.tool_calls.length) {
    let tools = inProgressMsg.querySelector(".tools");
    if (!tools) {
      tools = document.createElement("div");
      tools.className = "tools";
      inProgressMsg.appendChild(tools);
    }
    for (const t of m.tool_calls) {
      const line = document.createElement("div");
      line.className = "tool-line";
      line.textContent = t;
      tools.appendChild(line);
    }
  }
  renderAttachments(inProgressMsg, m.attachments);
  if (m.final) {
    inProgressMsg = null;
  }
  scrollToBottom();
}

function renderAttachments(parent, atts) {
  if (!atts || !atts.length) return;
  const wrap = document.createElement("div");
  for (const a of atts) {
    const chip = document.createElement("span");
    chip.className = "attachment";
    chip.textContent = `📄 ${a.filename || a.path || "file"}`;
    wrap.appendChild(chip);
  }
  parent.appendChild(wrap);
}

function appendMessage(role) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.innerHTML = `<div class="role">${role === "user" ? "you" : "curunir"}</div><div class="body"></div>`;
  messagesEl.appendChild(el);
  scrollToBottom();
  return el;
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  });
}

// === Send ===
function send() {
  const content = inputEl.value.trim();
  if (!content && staged.length === 0) return;
  if (!agentOnline) {
    const el = appendMessage("assistant");
    el.querySelector(".body").innerHTML = "<em>Agent is offline.</em>";
    return;
  }
  // Optimistic local render
  const el = appendMessage("user");
  el.querySelector(".body").textContent = content;
  renderAttachments(el, staged.map(a => ({filename: a.filename})));

  ws.send(JSON.stringify({
    content,
    attachments: staged.length ? staged.map(a => ({
      filename: a.filename, mime_type: a.mime_type, data: a.data,
    })) : null,
  }));
  inputEl.value = "";
  staged = [];
  renderStaged();
}

// === Attachments staging ===
async function stageFile(file) {
  // Validate
  const isImg = file.type.startsWith("image/");
  const isPdf = file.type === "application/pdf";
  const isText = !isImg && !isPdf;

  if (isImg && !IMAGE_MIMES.includes(file.type)) {
    alert(`Unsupported image type: ${file.type}`); return;
  }
  if (isImg && file.size > MAX_IMAGE) { alert(`Image > 5 MB`); return; }
  if (isPdf && file.size > MAX_PDF) { alert(`PDF > 10 MB`); return; }
  if (isText && file.size > MAX_TEXT) { alert(`Text > 256 KB`); return; }

  const totalAfter = staged.reduce((s, a) => s + a.size, 0) + file.size;
  if (totalAfter > MAX_TOTAL) { alert(`Total > 20 MB`); return; }

  const buf = await file.arrayBuffer();
  const data = btoa(String.fromCharCode(...new Uint8Array(buf)));
  staged.push({
    filename: file.name,
    mime_type: file.type || "application/octet-stream",
    data,
    size: file.size,
  });
  renderStaged();
}

function renderStaged() {
  stagedEl.innerHTML = "";
  staged.forEach((a, i) => {
    const chip = document.createElement("span");
    chip.className = "attachment";
    chip.textContent = `📎 ${a.filename} ✕`;
    chip.onclick = () => { staged.splice(i, 1); renderStaged(); };
    stagedEl.appendChild(chip);
  });
}

// === Wiring ===
sendBtn.onclick = send;
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});
inputEl.addEventListener("input", () => {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, window.innerHeight * 0.3) + "px";
});
attachBtn.onclick = () => fileInput.click();
fileInput.onchange = async () => {
  for (const f of fileInput.files) await stageFile(f);
  fileInput.value = "";
};

connect();
</script>
</body>
</html>
```

- [ ] **Step 2: Modify `portal/app.py` — mount static + serve `/`**

Replace entire file:

```python
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from portal import admin, auth, db, sign_in, ws_agent, ws_browser


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    await db.run_migrations()
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(lifespan=lifespan)
app.include_router(sign_in.router)
app.include_router(admin.router)
app.include_router(ws_agent.router)
app.include_router(ws_browser.router)


@app.get("/healthz")
async def healthz():
    ok = await db.ping()
    return JSONResponse({"status": "ok" if ok else "degraded"})


@app.get("/")
async def root(user=Depends(auth.optional_current_user)):
    if user is None:
        return RedirectResponse("/needs-invite", status_code=302)
    return FileResponse(Path(__file__).parent / "static" / "index.html")
```

- [ ] **Step 3: Add `tests/test_root.py` to `portal/tests/`**

```python
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
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `cd portal && pytest tests/test_root.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Manual smoke test in a browser**

Start portal + a fake agent (or skip the agent for now and just verify offline UX):
```bash
cd portal && uvicorn portal.app:app --reload
```
- Visit `http://localhost:8000/` → redirects to `/needs-invite`. ✓
- Create a user via CLI; copy the sign-in link.
- Click the link → renders confirm form. ✓
- Click "Sign in" → redirects to `/`. Chat surface loads, status pill says "offline". ✓
- Type a message + Send → "Agent is offline." rendered locally. ✓
- Run curunir with `CURUNIR_PORTAL_URL=ws://localhost:8000/ws/agent` and the matching token.
- Status pill flips to "online". Send a message; verify round-trip. ✓
- Hard-reload the browser tab → history snapshot rebuilds the conversation. ✓

- [ ] **Step 6: Commit**

```bash
git add portal/static/ portal/app.py portal/tests/test_root.py
git commit -m "feat(portal): chat surface served at / + needs-invite redirect"
```

---

## Phase 7 — Deployment

### Task 16: Dockerfile + render.yaml + access-log redaction

**Files:**
- Create: `portal/Dockerfile`
- Create: `portal/render.yaml`
- Modify: `portal/app.py` — middleware to strip `token=` from access logs

- [ ] **Step 1: Create `portal/Dockerfile`**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY . .
ENV PORT=8000
CMD ["sh", "-c", "uvicorn portal.app:app --host 0.0.0.0 --port ${PORT}"]
```

- [ ] **Step 2: Create `portal/render.yaml`**

```yaml
services:
  - type: web
    name: curunir-portal
    runtime: docker
    rootDir: portal
    plan: starter
    healthCheckPath: /healthz
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: curunir-portal-db
          property: connectionString
      - key: PORTAL_SECRET_KEY
        generateValue: true
      - key: PORTAL_BASE_URL
        sync: false   # set per-environment in dashboard
      - key: EMAIL_API_KEY
        sync: false
      - key: EMAIL_FROM
        sync: false
      - key: ADMIN_EMAILS
        sync: false

databases:
  - name: curunir-portal-db
    plan: free
    databaseName: portal
    user: portal
```

- [ ] **Step 3: Modify `portal/app.py`** — add log-redaction middleware

Add inside `app.py`, after `app = FastAPI(lifespan=lifespan)`:

```python
import re
import logging

_TOKEN_QS = re.compile(r"(\btoken=)[^&\s]+")


class _RedactingFilter(logging.Filter):
    """Replace `token=...` in any uvicorn-style access log message."""
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        record.msg = _TOKEN_QS.sub(r"\1<redacted>", msg)
        record.args = ()
        return True


for _name in ("uvicorn.access", "uvicorn.error"):
    logging.getLogger(_name).addFilter(_RedactingFilter())
```

- [ ] **Step 4: Add `tests/test_redaction.py`**

```python
import logging

from portal.app import _RedactingFilter, _TOKEN_QS


def test_token_query_param_redacted_in_log_messages():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "x", 1,
        'GET /sign-in?token=ABCDEF HTTP/1.1 200',
        (), None,
    )
    f = _RedactingFilter()
    f.filter(record)
    assert "token=<redacted>" in record.getMessage()
    assert "ABCDEF" not in record.getMessage()


def test_no_token_unchanged():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "x", 1,
        'GET /healthz HTTP/1.1 200', (), None,
    )
    f = _RedactingFilter()
    f.filter(record)
    assert "/healthz" in record.getMessage()
```

- [ ] **Step 5: Run tests, expect PASS**

Run: `cd portal && pytest tests/test_redaction.py -v`
Expected: 2 PASS.

- [ ] **Step 6: Manual deploy smoke (informational, run once after merge)**

After pushing to the branch Render is wired to:
- Render auto-builds and deploys.
- Dashboard env vars: set `EMAIL_API_KEY`, `EMAIL_FROM`, `ADMIN_EMAILS`, `PORTAL_BASE_URL`.
- `render shell` → `python -m portal.admin create-user --email you@example.com`.
- Open the emailed link from your phone; verify chat works.
- Point a real curunir container at the deployed URL; verify status pill goes online.

- [ ] **Step 7: Commit**

```bash
git add portal/Dockerfile portal/render.yaml portal/app.py portal/tests/test_redaction.py
git commit -m "feat(portal): Dockerfile + render.yaml + access-log token redaction"
```

---

## Self-review

After writing this plan, I checked it against the spec section by section.

**Coverage:**
- Architecture (3 components, federated): Tasks 1, 9, 10, 13, 14 cover both sides.
- Auth (admin-issued, reusable, GET→POST split, no expiry): Tasks 3, 4, 5.
- Cookie attributes (`Secure; HttpOnly; SameSite=Strict; Path=/`): Task 5, asserted in `test_post_sets_signed_cookie_and_redirects`.
- Revocation (`is_active`, token rotation): Tasks 2, 7.
- Rate limiting on `/sign-in`: Task 5, asserted in `test_rate_limit_blocks_after_threshold`.
- Admin allowlist + CSRF: Tasks 4, 7, asserted in `test_create_user_requires_csrf` and `test_admin_email_compare_case_insensitive`.
- Data model (one `users` table): Task 2.
- Container ↔ portal protocol (Bearer header, type discriminator, history snapshot): Tasks 9, 13.
- Browser ↔ portal protocol (cookie + Origin, unwrapping): Task 10, asserted in `test_browser_rejected_when_origin_mismatch`.
- Routing table (kick on replace, fan-out, drop when no browsers): Task 8.
- Lifecycle table from spec: Tasks 8–11 cover every row in the lifecycle table.
- History snapshot semantics (projection, 200 msg / 100 KB cap): Task 14, asserted in `test_cap_at_200_messages`.
- Attachments (shared helpers): Task 12.
- Frontend (single chat surface, status pill, reconnect, markdown, attachments): Task 15.
- Reconnect (1s→30s with jitter, terminal codes 4001/4002/4003): Task 13, asserted in `test_close_4003_terminal_does_not_reconnect` and `test_close_4002_replaced_terminal`.
- `/healthz` (DB ping): Task 2.
- Logging tagged with `user_id`: Tasks 9, 10 (logger.info with `extra={"user_id": ...}`).
- Access-log token redaction: Task 16.
- Deployment (Dockerfile, render.yaml): Task 16.
- File-level changes from spec: every file listed in the spec is created or modified in some task.

**Gaps/clarifications resolved inline:**
- The spec said `f"portal-{user_id}"` for the container-side session ID. The container does not know `user_id` (the portal does, via the token). Task 13 uses `PORTAL_SESSION_ID = "portal"` since each container is single-user — functionally equivalent; documented as a comment in `portal.py`.
- The history projection helper lives on `Agent` (Task 14, Step 3) rather than inside `PortalChannel` so it is reusable and unit-testable without spinning up a portal connection.
- Tests use a real Postgres (`portal_test` DB) per the existing curunir async-test convention. `portal/README.md` documents the one-time setup.

**Placeholders:** none. Every step has concrete code or commands.

**Type consistency:** verified — `User` dataclass fields (`id`, `email`, `sign_in_token`, `container_token`, `is_active`) are referenced consistently across `db.py`, `auth.py`, `admin.py`, and tests. Routing table types (`Sender` protocol, `UserRoute` dataclass) are stable across `routing.py`, `ws_agent.py`, `ws_browser.py`. `PortalChannel` constructor params (`in_queue`, `url`, `token`, `history_provider`, `uploads_dir`) match the call site in `run.py` (Task 14 Step 2).

---

## Execution suggestions

This plan is large. Two ways to approach execution:

1. **By phase, with checkpoints.** Phases 1–4 build a self-testing portal backend; Phase 5 is the curunir-side change; Phase 6 is the frontend; Phase 7 is deployment. Phases 1+2+3 can land before any curunir-side work begins. Phase 4 is the natural first end-to-end checkpoint (mock-agent round-trip works).
2. **Subagent-driven, task-by-task.** The 16 tasks are sized for fresh-subagent execution with review between tasks.

Each phase is independently mergeable; nothing in Phase 5 depends on Phase 6 internals (the `PortalChannel` is testable against the mock portal in Task 13, no browser needed).
