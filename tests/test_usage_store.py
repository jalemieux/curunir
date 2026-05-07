# tests/test_usage_store.py
from datetime import datetime, timedelta, timezone

import pytest

from src.usage_store import UsageRecord, UsageStore


def _record(
    *,
    ts: datetime | None = None,
    session_id: str = "s1",
    model: str = "openrouter/anthropic/claude-sonnet-4",
    prompt: int = 100,
    completion: int = 50,
    cached: int = 0,
    reasoning: int = 0,
    image: int = 0,
    audio: int = 0,
    cost: float | None = 0.001,
    elapsed: float = 0.5,
) -> UsageRecord:
    return UsageRecord(
        ts=ts or datetime.now(timezone.utc),
        session_id=session_id,
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_prompt_tokens=cached,
        reasoning_tokens=reasoning,
        image_tokens=image,
        audio_tokens=audio,
        cost_usd=cost,
        elapsed_sec=elapsed,
    )


def test_schema_created_on_first_use(tmp_path):
    db = tmp_path / "usage.db"
    store = UsageStore(db)
    store.record(_record())
    # Reopening should still see the row.
    store2 = UsageStore(db)
    rows = store2.summary(timedelta(days=1))
    assert rows
    assert rows[0]["calls"] == 1


def test_record_and_summary_roundtrip(tmp_path):
    store = UsageStore(tmp_path / "usage.db")
    now = datetime.now(timezone.utc)

    store.record(_record(ts=now, model="m1", prompt=100, completion=50, cost=0.01))
    store.record(_record(ts=now, model="m1", prompt=200, completion=70, cost=0.02))
    store.record(_record(ts=now, model="m2", prompt=10, completion=5, cost=0.001))
    # Outside the 1d window:
    store.record(_record(ts=now - timedelta(days=3), model="m1", cost=0.99))

    by_model = {r["model"]: r for r in store.summary(timedelta(days=1), group_by="model")}
    assert set(by_model) == {"m1", "m2"}
    assert by_model["m1"]["calls"] == 2
    assert by_model["m1"]["prompt_tokens"] == 300
    assert by_model["m1"]["completion_tokens"] == 120
    assert by_model["m1"]["cost_usd"] == pytest.approx(0.03)
    assert by_model["m2"]["calls"] == 1
    assert by_model["m2"]["cost_usd"] == pytest.approx(0.001)


def test_null_cost_handled(tmp_path):
    store = UsageStore(tmp_path / "usage.db")
    store.record(_record(model="m1", cost=None))
    store.record(_record(model="m1", cost=0.05))
    rows = {r["model"]: r for r in store.summary(timedelta(days=1), group_by="model")}
    # NULLs should sum as 0, not propagate.
    assert rows["m1"]["cost_usd"] == pytest.approx(0.05)
    assert rows["m1"]["calls"] == 2


def test_cli_prints_summary(tmp_path, capsys):
    from src.usage import main as usage_main

    store = UsageStore(tmp_path / "usage.db")
    store.record(_record(model="m1", cost=0.01))
    store.record(_record(model="m2", cost=0.02))

    rc = usage_main(["--db", str(tmp_path / "usage.db"), "--window", "1d"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "m1" in out and "m2" in out
    assert "TOTAL" in out


def test_cli_missing_db_returns_error(tmp_path, capsys):
    from src.usage import main as usage_main

    rc = usage_main(["--db", str(tmp_path / "missing.db")])
    err = capsys.readouterr().err
    assert rc == 1
    assert "No usage database" in err


def test_summary_group_by_day(tmp_path):
    store = UsageStore(tmp_path / "usage.db")
    now = datetime.now(timezone.utc)
    store.record(_record(ts=now, model="m1", cost=0.01))
    store.record(_record(ts=now - timedelta(days=1, hours=1), model="m1", cost=0.02))
    rows = store.summary(timedelta(days=7), group_by="day")
    assert len(rows) == 2
    days = {r["day"] for r in rows}
    assert len(days) == 2
