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

- Docker (with `docker compose`)
- A curunir checkout (this repo)

The recommended local-dev path is fully containerized — Docker runs both
Postgres and the portal app. You do not need a Python venv unless you want
to run the test suite (see [Tests](#tests)).

## Local development

The portal's compose stack is merged into the root `docker-compose.yml`.
All `docker compose` commands below run from the **repo root**.

### 1. Configure env vars

Create `portal/.env`:

```bash
# Required
PORTAL_SECRET_KEY=any-long-random-string-not-the-default
ADMIN_EMAILS=you@example.com           # comma-separated allowlist
DEBUG=true                             # required for http://localhost — see note below

# Defaulted (override only if needed)
PORTAL_BASE_URL=http://localhost:8000

# Optional — leave blank to skip real email; sign-in links print to logs
EMAIL_API_KEY=
EMAIL_FROM=noreply@example.com
```

`DATABASE_URL` is set automatically by the root `docker-compose.yml` when
you run via Docker (`postgres:5432` on the compose network). If you run
the portal natively against the dockerized Postgres, set
`DATABASE_URL=postgresql://postgres:postgres@localhost:5432/portal`.

> **Why `DEBUG=true` for local dev:** the session cookie is issued with the
> `Secure` flag in production, which browsers refuse to store over plain
> HTTP — including `http://localhost` in Safari and recent Chrome. Without
> `DEBUG=true`, you'd click the sign-in button, the cookie would silently be
> dropped, and the redirect to `/` would bounce you back to `/needs-invite`.
> In production (HTTPS), leave `DEBUG` unset so `Secure` is enforced.

### 2. Bring the stack up

From the repo root:

```bash
docker compose up -d --build            # postgres + portal + curunir
docker compose ps                       # all services Up, postgres healthy
docker compose logs -f portal           # watch boot; Ctrl-C exits the log stream
```

To start just postgres + portal (skip curunir for now):

```bash
docker compose up -d --build postgres portal
```

The portal listens on `http://localhost:8000`; visit `/healthz` to
confirm `{"status":"ok"}`. Source under `portal/` is bind-mounted into the
container, so `uvicorn --reload` picks up edits live.

If you hit a "port 5432 already allocated" error, another Postgres
container or process owns the port. Find it with
`lsof -iTCP:5432 -sTCP:LISTEN` (or `docker ps | grep 5432`) and stop it,
or remap the host port in `docker-compose.yml`.

### 3. Create your first user

```bash
docker compose exec portal python -m portal.admin create-user --email you@example.com
```

If `EMAIL_API_KEY` is unset, the CLI prints the sign-in link directly.
Paste it into your browser — you'll see a "Sign in as you@example.com?"
page; clicking sets the session cookie and lands you on the chat surface.

The CLI also prints the **container token** — copy it; you'll need it in
step 4. (It's only shown once.)

### Quick start: dev seed (skip step 3 + 4 setup)

If you just want to bring everything up locally for testing, the root
`docker-compose.yml` already wires a dev seed:

- Portal default `SEED_USER_EMAIL=dev@example.com`,
  `SEED_CONTAINER_TOKEN=dev-seed-token-change-me`.
- Curunir default `CURUNIR_PORTAL_TOKEN=dev-seed-token-change-me` (matches).

When `DEBUG=true` is set in `portal/.env`, the portal lifespan idempotently
upserts the seed user with that exact token on startup, so `docker compose
up --build` produces a working portal ↔ curunir round-trip without any
manual CLI steps. To use a non-default token, set `CURUNIR_PORTAL_TOKEN`
in the **root** `.env` — both services pick it up via interpolation.

For a real (non-dev) workflow, leave `DEBUG` unset and use the manual
flow below.

### 4. Wire curunir to the portal

The curunir service in the root `docker-compose.yml` already defaults
`CURUNIR_PORTAL_URL=ws://portal:8000/ws/agent` (the in-network address).
You just need to set the container token in the root `.env`:

```bash
# In <repo-root>/.env
CURUNIR_PORTAL_TOKEN=<container-token-from-step-3>
```

Then restart curunir:

```bash
docker compose up -d curunir            # picks up the new token
```

The status pill in the browser flips from "offline" → "online" once the
container connects. Type a message to round-trip a chat through the portal.

To run curunir natively on the host instead (useful for fast iteration),
start only postgres + portal in Docker and run curunir from your venv:

```bash
docker compose up -d postgres portal
CURUNIR_PORTAL_URL=ws://localhost:8000/ws/agent \
CURUNIR_PORTAL_TOKEN=<container-token-from-step-3> \
python run.py
```

### Common operations

```bash
docker compose down                     # stop services (data persists in the named volume)
docker compose down -v                  # stop and wipe the postgres volume (fresh DB)
docker compose restart portal           # restart just the portal app
docker compose exec portal sh           # shell into the portal container
docker compose exec postgres psql -U postgres -d portal   # ad-hoc SQL
```

## Running the portal natively (alternative)

If you'd rather run the portal app outside Docker (e.g. for IDE debugging),
keep Postgres in Docker and run uvicorn from your venv:

```bash
# From repo root with curunir venv active
pip install \
  "fastapi>=0.110" "uvicorn[standard]>=0.29" "asyncpg>=0.29" \
  "itsdangerous>=2.2" "jinja2>=3.1" "httpx>=0.27" \
  "python-multipart>=0.0.9" "pydantic-settings>=2.2" "websockets>=12.0"

docker compose up -d postgres                # Postgres only (from repo root)
uvicorn portal.app:app --reload --port 8000
```

In this mode set `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/portal`
in `portal/.env`, and run the create-user CLI from the repo root:
`PYTHONPATH=. python -m portal.admin create-user --email you@example.com`.

## Tests

Tests run from your host venv (not from the portal container — the image
intentionally doesn't ship dev deps). Both suites use the dockerized
Postgres on `localhost:5432` (separate `portal_test` db).

```bash
# One-time: create the test database
docker compose exec postgres createdb -U postgres portal_test

# Portal tests (53 tests) — from <repo-root>/portal
cd portal && pytest

# Curunir-side portal channel tests — from <repo-root>
pytest tests/test_portal_channel.py tests/test_history_snapshot.py
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
