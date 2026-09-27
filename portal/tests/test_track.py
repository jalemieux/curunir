import pytest

from portal import db
from portal.track import _rate_buckets, hash_ip


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    _rate_buckets.clear()
    yield
    _rate_buckets.clear()


async def _rows():
    async with db.get_pool().acquire() as conn:
        return await conn.fetch(
            "SELECT variant, event_type, ip_hash, user_agent FROM variant_events ORDER BY id"
        )


@pytest.mark.asyncio
async def test_track_stores_event_with_hashed_ip(client):
    resp = await client.post(
        "/track",
        json={"variant": "pos-0925-a", "event_type": "page_view"},
        headers={"x-forwarded-for": "203.0.113.7", "user-agent": "test-ua"},
    )
    assert resp.status_code == 204
    assert resp.content == b""
    assert "set-cookie" not in resp.headers
    rows = await _rows()
    assert len(rows) == 1
    assert rows[0]["variant"] == "pos-0925-a"
    assert rows[0]["event_type"] == "page_view"
    assert rows[0]["user_agent"] == "test-ua"
    # Hashed, never raw.
    assert rows[0]["ip_hash"] == hash_ip("203.0.113.7")
    assert "203.0.113.7" not in rows[0]["ip_hash"]
    assert len(rows[0]["ip_hash"]) == 64


@pytest.mark.asyncio
async def test_track_counts_per_variant_and_event(client):
    events = [
        ("pos-a", "page_view"), ("pos-a", "page_view"), ("pos-a", "cta_click"),
        ("pos-b", "page_view"),
    ]
    for variant, event_type in events:
        r = await client.post("/track", json={"variant": variant, "event_type": event_type})
        assert r.status_code == 204
    assert await db.variant_event_counts() == [
        ("pos-a", "cta_click", 1),
        ("pos-a", "page_view", 2),
        ("pos-b", "page_view", 1),
    ]


@pytest.mark.parametrize("body", [
    {"variant": "pos-a", "event_type": "scroll"},
    {"variant": "Pos_A", "event_type": "page_view"},
    {"variant": "<script>", "event_type": "page_view"},
    {"variant": "x" * 41, "event_type": "page_view"},
    {"variant": "", "event_type": "page_view"},
    {"event_type": "page_view"},
    {"variant": "pos-a"},
])
@pytest.mark.asyncio
async def test_track_rejects_bad_input(client, body):
    resp = await client.post("/track", json=body)
    assert resp.status_code == 422
    assert await _rows() == []


@pytest.mark.asyncio
async def test_track_rate_limit(client, monkeypatch):
    from portal.config import settings
    monkeypatch.setattr(settings, "rate_limit_per_min", 2)
    body = {"variant": "pos-a", "event_type": "page_view"}
    for _ in range(2):
        assert (await client.post("/track", json=body)).status_code == 204
    r = await client.post("/track", json=body)
    assert r.status_code == 429
    assert len(await _rows()) == 2


@pytest.mark.asyncio
async def test_track_bucket_independent_of_beta_signup(client, monkeypatch):
    from portal.beta import _rate_buckets as beta_buckets
    from portal.config import settings
    monkeypatch.setattr(settings, "rate_limit_per_min", 1)
    beta_buckets.clear()
    try:
        body = {"variant": "pos-a", "event_type": "page_view"}
        assert (await client.post("/track", json=body)).status_code == 204
        r = await client.post("/beta/signup", json={"email": "a@example.com", "source": "pos-a"})
        assert r.status_code == 200
    finally:
        beta_buckets.clear()
