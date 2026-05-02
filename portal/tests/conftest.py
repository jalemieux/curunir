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
