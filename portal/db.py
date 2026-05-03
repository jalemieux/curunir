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
