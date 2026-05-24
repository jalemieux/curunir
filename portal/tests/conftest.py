import os

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# Force test settings before importing the app.
os.environ.setdefault("PORTAL_SECRET_KEY", "test-secret-do-not-use")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/portal_test",
)
os.environ.setdefault("ADMIN_EMAILS", "admin@example.com")
os.environ.setdefault("PORTAL_BASE_URL", "http://localhost:8000")
# Force-override regardless of dev .env: tests assume debug=False so Secure
# cookies are emitted (test_post_sets_signed_cookie_and_redirects).
os.environ["DEBUG"] = "false"

from portal import db as portal_db  # noqa: E402
from portal.app import app  # noqa: E402


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(client):
    """After each test, truncate users."""
    yield
    try:
        pool = portal_db.get_pool()
    except RuntimeError:
        return
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE users, beta_signups RESTART IDENTITY")
