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


@dataclass
class BetaSignup:
    id: int
    email: str
    message: Optional[str]
    source: Optional[str]
    ip: Optional[str]
    user_agent: Optional[str]
    created_at: object  # datetime, kept loose to avoid importing


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
    migrations_dir = Path(__file__).parent / "migrations"
    files = sorted(migrations_dir.glob("*.sql"))
    async with get_pool().acquire() as conn:
        for f in files:
            await conn.execute(f.read_text())


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


async def upsert_user_with_container_token(
    email: str, container_token: str
) -> User:
    """Idempotent seed for dev. If the email exists, refreshes its
    container token and reactivates; otherwise creates the row with a
    fresh sign-in token alongside the given container token.
    """
    sign_in_token = make_token()
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (email, sign_in_token, container_token, is_active)
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (email) DO UPDATE
              SET container_token = EXCLUDED.container_token,
                  is_active = TRUE
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


# ---------- Beta signups ----------

def _row_to_beta_signup(row: asyncpg.Record) -> BetaSignup:
    return BetaSignup(
        id=row["id"],
        email=row["email"],
        message=row["message"],
        source=row["source"],
        ip=row["ip"],
        user_agent=row["user_agent"],
        created_at=row["created_at"],
    )


async def create_beta_signup(
    email: str,
    message: Optional[str] = None,
    source: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> tuple[BetaSignup, bool]:
    """Insert a beta signup. Returns (row, created) where `created` is False
    if the email was already on the list (idempotent)."""
    normalized = email.strip().lower()
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO beta_signups (email, message, source, ip, user_agent)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (LOWER(email)) DO NOTHING
            RETURNING id, email, message, source, ip, user_agent, created_at
            """,
            normalized, message, source, ip, user_agent,
        )
        if row is not None:
            return _row_to_beta_signup(row), True
        existing = await conn.fetchrow(
            """
            SELECT id, email, message, source, ip, user_agent, created_at
            FROM beta_signups WHERE LOWER(email) = $1
            """,
            normalized,
        )
        return _row_to_beta_signup(existing), False


async def list_beta_signups() -> list[BetaSignup]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, email, message, source, ip, user_agent, created_at
            FROM beta_signups ORDER BY created_at DESC
            """
        )
    return [_row_to_beta_signup(r) for r in rows]
