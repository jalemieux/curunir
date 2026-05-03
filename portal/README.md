# Curunir Portal

Hosted multi-user chat surface for curunir. A standalone FastAPI app that
authenticates browsers via signed-cookie sessions and routes messages to a
self-hosted curunir container over WebSocket. The portal stores no chat
content — only `users` rows in Postgres.

## Architecture in 30 seconds

```
Browser ──wss──> Portal ──wss──> Curunir container
         (cookie)        (Bearer token)
```

The container dials *out* to the portal on startup. The portal multiplexes
each browser session to the matching container. See `docs/superpowers/plans/2026-04-30-curunir-portal.md`
for the full design.

## Prerequisites

- Python 3.12+
- Docker (for local Postgres)
- A curunir checkout (this repo) with a working venv

## Local development

All commands assume your shell is at the **repo root** (the directory
containing both `portal/` and `src/`), and that you have a venv activated
with the curunir deps installed (`pip install -r requirements.txt`).

### 1. Install portal dependencies

The portal package is laid out as `portal/__init__.py` + sibling modules,
so editable installs (`pip install -e portal/`) don't work cleanly — install
the deps directly instead:

```bash
pip install \
  "fastapi>=0.110" "uvicorn[standard]>=0.29" "asyncpg>=0.29" \
  "itsdangerous>=2.2" "jinja2>=3.1" "httpx>=0.27" \
  "python-multipart>=0.0.9" "pydantic-settings>=2.2" "websockets>=12.0"
```

### 2. Start Postgres

```bash
cd portal && docker compose up -d
```

This starts Postgres 16 on `localhost:5432` (user `postgres`, password
`postgres`, db `portal`). The portal app runs schema migrations on startup.

If you also want to run the test suite, create the test database once:

```bash
docker compose exec postgres createdb -U postgres portal_test
```

### 3. Configure env vars

Create `portal/.env` (or export in your shell):

```bash
# Required
PORTAL_SECRET_KEY=any-long-random-string-not-the-default
ADMIN_EMAILS=you@example.com           # comma-separated allowlist
DEBUG=true                             # required for http://localhost — see note below

# Defaulted (override only if needed)
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/portal
PORTAL_BASE_URL=http://localhost:8000

# Optional — leave blank to skip real email; sign-in links print to logs
EMAIL_API_KEY=
EMAIL_FROM=noreply@example.com
```

> **Why `DEBUG=true` for local dev:** the session cookie is issued with the
> `Secure` flag in production, which browsers refuse to store over plain
> HTTP — including `http://localhost` in Safari and recent Chrome. Without
> `DEBUG=true`, you'd click the sign-in button, the cookie would silently be
> dropped, and the redirect to `/` would bounce you back to `/needs-invite`.
> In production (HTTPS), leave `DEBUG` unset so `Secure` is enforced.

### 4. Run the portal

From the **repo root** (so the `portal` package is importable):

```bash
uvicorn portal.app:app --reload --port 8000
```

Or from inside `portal/`:

```bash
cd portal && PYTHONPATH=.. uvicorn portal.app:app --reload --port 8000
```

Visit `http://localhost:8000/healthz` — should return `{"status":"ok"}`.

### 5. Create your first user (CLI)

```bash
PYTHONPATH=. python -m portal.admin create-user --email you@example.com
```

If `EMAIL_API_KEY` is unset, the CLI prints the sign-in link directly. Copy
it into your browser; you'll see a "Sign in as you@example.com?" page.
Click the button to set the session cookie and land on the chat surface.

The CLI also prints the **container token** — copy it; you'll need it in
step 6. (It's only shown once.)

### 6. Connect a curunir container

In a separate shell, point a curunir process at the portal:

```bash
cd /path/to/curunir
CURUNIR_PORTAL_URL=ws://localhost:8000/ws/agent \
CURUNIR_PORTAL_TOKEN=<container-token-from-step-5> \
python run.py
```

The status pill in the browser flips from "offline" → "online" once the
container connects. Type a message to round-trip a chat through the portal.

## Tests

Both suites use the same Postgres instance (separate `portal_test` db).

```bash
# Portal tests (53 tests)
cd portal && pytest

# Curunir-side portal channel tests (5 tests in test_portal_channel.py + 5 in test_history_snapshot.py)
cd <repo-root> && pytest tests/test_portal_channel.py tests/test_history_snapshot.py
```

The portal test suite uses `pythonpath = [".."]` in `pyproject.toml` to
make the `portal` package importable from the test directory.

## Admin UI

After signing in as an admin (an email listed in `ADMIN_EMAILS`), visit
`http://localhost:8000/admin`. From there you can:

- Create new users (auto-emails sign-in link if `EMAIL_API_KEY` is set)
- Resend a sign-in link
- Rotate the sign-in token (invalidates the old link)
- Rotate the container token (forces the container to re-authenticate)
- Deactivate a user (revokes both tokens)

## Deploy

Render auto-deploys from the linked branch using `portal/render.yaml`,
which provisions a Postgres add-on and the web service. Set these in the
Render dashboard before the first deploy:

- `PORTAL_BASE_URL` — your public portal URL (e.g., `https://curunir-portal.onrender.com`)
- `EMAIL_API_KEY` — Postmark server token
- `EMAIL_FROM` — verified sender address
- `ADMIN_EMAILS` — comma-separated admin allowlist

`PORTAL_SECRET_KEY` is auto-generated by Render. `DATABASE_URL` is wired
from the linked Postgres.

After deploy, create your first admin user from the Render shell:

```bash
python -m portal.admin create-user --email you@example.com
```
