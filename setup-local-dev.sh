#!/usr/bin/env bash
# Set up a local dev environment for curunir + portal on a fresh machine.
#
# Creates both virtualenvs, installs dependencies, and scaffolds portal/.env.
# Idempotent: safe to re-run — never overwrites an existing venv or .env file.
#
# Usage:
#   ./setup-local-dev.sh
#   PORTAL_ADMIN_EMAIL=you@example.com ./setup-local-dev.sh   # non-interactive
set -euo pipefail

cd "$(dirname "$0")"

# Curunir requires Python 3.12+ (portal config parsing uses tomllib).
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "error: Python 3.12+ required (found $(python3 -V 2>&1))" >&2
  exit 1
fi

echo "==> Curunir venv (.venv)"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
echo "    deps installed"

echo "==> Portal venv (portal/.venv)"
[ -d portal/.venv ] || python3 -m venv portal/.venv
portal/.venv/bin/pip install -q --upgrade pip
# Install portal deps straight from portal/pyproject.toml. No editable install:
# the portal isn't structured as an installable package, so setuptools' flat
# layout discovery fails on it. Reading the dep list keeps this drift-free.
portal/.venv/bin/python - <<'PY'
import subprocess, sys, tomllib
from pathlib import Path

project = tomllib.loads(Path("portal/pyproject.toml").read_text())["project"]
deps = list(project["dependencies"])
deps += project.get("optional-dependencies", {}).get("dev", [])
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *deps])
PY
echo "    deps installed"

echo "==> portal/.env"
if [ -f portal/.env ]; then
  echo "    exists — leaving as-is"
else
  admin_email="${PORTAL_ADMIN_EMAIL:-}"
  if [ -z "$admin_email" ] && [ -t 0 ]; then
    read -rp "    Admin email for the portal: " admin_email
  fi
  admin_email="${admin_email:-you@example.com}"
  secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  cat > portal/.env <<EOF
# Portal local-dev config. Not committed (gitignored).
PORTAL_SECRET_KEY=${secret}
ADMIN_EMAILS=${admin_email}
DEBUG=true
PORTAL_BASE_URL=http://localhost:8000

# Portal runs natively against the dockerized Postgres on the host port.
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/portal

# Dev seed: with DEBUG=true the portal upserts this user (admin + known
# container token) on startup, so curunir can authenticate with no CLI step.
SEED_USER_EMAIL=${admin_email}
SEED_CONTAINER_TOKEN=dev-seed-token-change-me

# Optional — leave blank to skip real email; sign-in links print to logs.
EMAIL_API_KEY=
EMAIL_FROM=noreply@example.com
EOF
  echo "    created (admin=${admin_email})"
fi

cat <<'EOF'

Setup complete. Remaining manual steps (both are gitignored — copy from
another machine or scaffold from the examples):

  1. Root .env — copy your API keys over, and ensure it contains:
       CURUNIR_PORTAL_URL=ws://localhost:8000/ws/agent
       CURUNIR_PORTAL_TOKEN=dev-seed-token-change-me
  2. context/ — run ./sync-context.sh (curunir needs context/identity.md)

Then run it (3 terminals, from the repo root):
  docker compose up -d postgres
  portal/.venv/bin/uvicorn portal.app:app --reload --port 8000
  source .venv/bin/activate && python run.py

Browser sign-in link (after the portal has booted once):
  docker compose exec postgres psql -U postgres -d portal -tAc \
    "SELECT 'http://localhost:8000/sign-in?token='||sign_in_token FROM users LIMIT 1;"
EOF
