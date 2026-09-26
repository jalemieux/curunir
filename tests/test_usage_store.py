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


def test_summary_group_by_session(tmp_path):
    store = UsageStore(tmp_path / "usage.db")
    now = datetime.now(timezone.utc)
    store.record(_record(ts=now, session_id="conv-a", prompt=100, completion=10))
    store.record(_record(ts=now, session_id="conv-a", prompt=50, completion=5))
    store.record(_record(ts=now, session_id="conv-b", prompt=10, completion=1))
    rows = {r["session"]: r for r in store.summary(timedelta(days=1), group_by="session")}
    assert set(rows) == {"conv-a", "conv-b"}
    assert rows["conv-a"]["calls"] == 2
    assert rows["conv-a"]["prompt_tokens"] == 150
    assert rows["conv-a"]["completion_tokens"] == 15


def test_summary_session_collapses_scheduled_runs(tmp_path):
    """Each scheduled run gets a unique ``sched:<id>:<ts>`` session; a job's
    runs must roll up under ``sched:<id>`` so the breakdown shows one row."""
    store = UsageStore(tmp_path / "usage.db")
    now = datetime.now(timezone.utc)
    store.record(_record(ts=now, session_id="sched:digest:1000", prompt=100, completion=10))
    store.record(_record(ts=now, session_id="sched:digest:2000", prompt=200, completion=20))
    store.record(_record(ts=now, session_id="sched:digest:3000", prompt=300, completion=30))
    rows = {r["session"]: r for r in store.summary(timedelta(days=1), group_by="session")}
    assert set(rows) == {"sched:digest"}
    assert rows["sched:digest"]["calls"] == 3
    assert rows["sched:digest"]["prompt_tokens"] == 600
    assert rows["sched:digest"]["completion_tokens"] == 60


def test_summary_session_sorted_by_tokens_desc(tmp_path):
    store = UsageStore(tmp_path / "usage.db")
    now = datetime.now(timezone.utc)
    store.record(_record(ts=now, session_id="small", prompt=10, completion=1))
    store.record(_record(ts=now, session_id="big", prompt=1000, completion=100))
    store.record(_record(ts=now, session_id="mid", prompt=200, completion=20))
    order = [r["session"] for r in store.summary(timedelta(days=1), group_by="session")]
    assert order == ["big", "mid", "small"]


def test_summary_group_by_day_session_no_collapse(tmp_path):
    """``day_session`` groups by (calendar-day, raw session_id) and — unlike
    ``session`` — does NOT collapse scheduled runs, so each firing is its own
    row for the By-day drill-down."""
    store = UsageStore(tmp_path / "usage.db")
    now = datetime.now(timezone.utc)
    earlier = now - timedelta(days=1, hours=1)
    # Two firings of the same job today + one yesterday + a normal convo today.
    store.record(_record(ts=now, session_id="sched:digest:1000", prompt=100, completion=10))
    store.record(_record(ts=now, session_id="sched:digest:2000", prompt=200, completion=20))
    store.record(_record(ts=earlier, session_id="sched:digest:500", prompt=50, completion=5))
    store.record(_record(ts=now, session_id="conv-a", prompt=10, completion=1))

    rows = store.summary(timedelta(days=7), group_by="day_session")
    # Each (day, raw session_id) is a distinct row — no sched collapse.
    keys = {(r["day"], r["session_id"]) for r in rows}
    assert len(rows) == 4
    today = now.date().isoformat()
    assert (today, "sched:digest:1000") in keys
    assert (today, "sched:digest:2000") in keys
    assert (today, "conv-a") in keys
    assert (earlier.date().isoformat(), "sched:digest:500") in keys


def test_summary_rejects_unknown_group_by(tmp_path):
    store = UsageStore(tmp_path / "usage.db")
    store.record(_record())
    with pytest.raises(ValueError):
        store.summary(timedelta(days=1), group_by="banana")


# --- agent column (agents-and-containers phase 2) ---------------------------

def test_agent_column_is_recorded_and_groupable(tmp_path):
    from datetime import datetime, timedelta, timezone

    store = UsageStore(tmp_path / "u.db")
    now = datetime.now(timezone.utc)
    store.record(UsageRecord(ts=now, session_id="a", model="m", prompt_tokens=10, completion_tokens=1, agent="finance"))
    store.record(UsageRecord(ts=now, session_id="b", model="m", prompt_tokens=20, completion_tokens=2, agent="finance"))
    store.record(UsageRecord(ts=now, session_id="c", model="m", prompt_tokens=5, completion_tokens=1))
    rows = {r["agent"]: r for r in store.summary(timedelta(days=1), group_by="agent")}
    assert rows["finance"]["prompt_tokens"] == 30 and rows["finance"]["calls"] == 2
    assert rows[""]["prompt_tokens"] == 5  # unstamped rows group under ""
    store.close()


def test_agent_column_is_added_to_a_pre_existing_database(tmp_path):
    import sqlite3
    from datetime import datetime, timedelta, timezone

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE usage (
          id INTEGER PRIMARY KEY, ts TEXT NOT NULL, session_id TEXT NOT NULL,
          model TEXT NOT NULL, prompt_tokens INTEGER NOT NULL,
          completion_tokens INTEGER NOT NULL,
          cached_prompt_tokens INTEGER NOT NULL DEFAULT 0,
          reasoning_tokens INTEGER NOT NULL DEFAULT 0,
          image_tokens INTEGER NOT NULL DEFAULT 0,
          audio_tokens INTEGER NOT NULL DEFAULT 0,
          cost_usd REAL, elapsed_sec REAL NOT NULL
        );
    """)
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO usage (ts, session_id, model, prompt_tokens, completion_tokens, elapsed_sec) "
        "VALUES (?, 'old', 'm', 7, 1, 0.5)", (ts,),
    )
    conn.commit()
    conn.close()

    store = UsageStore(db)  # opens + migrates
    cols = {r[1] for r in store._conn.execute("PRAGMA table_info(usage)")}
    assert "agent" in cols
    # the old row survived and reads back with a NULL agent
    rows = store.summary(timedelta(days=1), group_by="agent")
    assert rows == [dict(rows[0])] and rows[0]["agent"] == "" and rows[0]["prompt_tokens"] == 7
    store.record(UsageRecord(ts=datetime.now(timezone.utc), session_id="n", model="m",
                             prompt_tokens=1, completion_tokens=1, agent="everyday"))
    assert {r["agent"] for r in store.summary(timedelta(days=1), group_by="agent")} == {"", "everyday"}
    # reopening is idempotent (no duplicate-column error)
    store.close()
    UsageStore(db).close()
