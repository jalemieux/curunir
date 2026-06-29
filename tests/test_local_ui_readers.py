"""Tests for the local-web read adapters (src/local_ui/readers.py).

These are pure functions that wrap the *existing* read APIs (UsageStore,
portfolio.engine, scheduler._load_tasks, context/memory file walks) so the
co-located web UI shows the same numbers the CLIs do — and can't drift.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.config import AgentConfig
from src.local_ui import readers
from src.portfolio import db as pdb
from src.portfolio import engine
from src.schedule_store import db as sdb
from src.schedule_store import engine as sengine
from src.usage_store import UsageRecord, UsageStore


@pytest.fixture
def config(tmp_path):
    """An AgentConfig whose stores live under a temp dir."""
    ctx = tmp_path / "context"
    (ctx / "memory").mkdir(parents=True)
    return AgentConfig(
        identity_file=ctx / "identity.md",
        context_dir=ctx,
        usage_db=ctx / "usage.db",
        schedules_db=ctx / "schedules.db",
        portfolio_db=str(ctx / "memory" / "portfolio.db"),
    )


# --- usage_summary ---------------------------------------------------------


def _seed_usage(config):
    store = UsageStore(config.usage_db)
    now = datetime.now(timezone.utc)
    store.record(UsageRecord(
        ts=now, session_id="cli", model="anthropic/claude-x",
        prompt_tokens=100, completion_tokens=20, cost_usd=0.5, elapsed_sec=1.0,
    ))
    store.record(UsageRecord(
        ts=now, session_id="cli", model="anthropic/claude-x",
        prompt_tokens=50, completion_tokens=10, cost_usd=0.25, elapsed_sec=2.0,
    ))
    store.close()
    return store


def test_usage_summary_matches_store(config):
    _seed_usage(config)
    out = readers.usage_summary(config, window="7d", by="model")
    assert out["by"] == "model"
    assert out["window"] == "7d"
    assert len(out["rows"]) == 1
    row = out["rows"][0]
    assert row["model"] == "anthropic/claude-x"
    assert row["calls"] == 2
    assert row["prompt_tokens"] == 150
    assert row["completion_tokens"] == 30
    assert row["cost_usd"] == pytest.approx(0.75)


def test_usage_summary_equals_direct_store_call(config):
    _seed_usage(config)
    out = readers.usage_summary(config, window="7d", by="model")
    direct = UsageStore(config.usage_db).summary(timedelta(days=7), group_by="model")
    assert out["rows"] == direct


def test_usage_summary_missing_db(tmp_path):
    cfg = AgentConfig(usage_db=tmp_path / "nope.db")
    out = readers.usage_summary(cfg, window="7d", by="model")
    assert out["rows"] == []
    assert out.get("available") is False


def test_usage_summary_rejects_bad_window(config):
    _seed_usage(config)
    with pytest.raises(ValueError):
        readers.usage_summary(config, window="banana", by="model")


def test_usage_summary_by_session_labels_conversations(config):
    """``by=session`` rows are enriched with a human title + channel from the
    conversation store, and scheduled jobs are labeled by job id."""
    from src.agent import conversation_store

    store = UsageStore(config.usage_db)
    now = datetime.now(timezone.utc)
    store.record(UsageRecord(ts=now, session_id="conv1", model="m",
                             prompt_tokens=100, completion_tokens=10))
    store.record(UsageRecord(ts=now, session_id="sched:digest:1700000000", model="m",
                             prompt_tokens=50, completion_tokens=5))
    store.close()
    # A saved conversation gives conv1 a real title + channel.
    conversation_store.save(
        config.context_dir, "conv1",
        [{"role": "user", "content": "Help me rebalance my portfolio"}],
        channel="portal",
    )

    out = readers.usage_summary(config, window="7d", by="session")
    rows = {r["session"]: r for r in out["rows"]}
    assert rows["conv1"]["label"].startswith("Help me rebalance")
    assert rows["conv1"]["channel"] == "portal"
    # Scheduled job: collapsed to sched:digest, labeled by job id.
    assert rows["sched:digest"]["label"] == "digest"
    assert rows["sched:digest"]["channel"] == "sched"


def test_usage_summary_session_fallback_labels(config):
    """Sessions without a conversation record fall back to friendly/short labels."""
    store = UsageStore(config.usage_db)
    now = datetime.now(timezone.utc)
    store.record(UsageRecord(ts=now, session_id="cli", model="m",
                             prompt_tokens=100, completion_tokens=10))
    store.record(UsageRecord(ts=now, session_id="deadbeefcafebabe1234", model="m",
                             prompt_tokens=10, completion_tokens=1))
    store.close()

    out = readers.usage_summary(config, window="7d", by="session")
    rows = {r["session"]: r for r in out["rows"]}
    assert rows["cli"]["label"] == "CLI"
    assert rows["cli"]["channel"] == "cli"
    # Unknown chat session: short id prefix, generic channel.
    assert rows["deadbeefcafebabe1234"]["label"] == "deadbeef"
    assert rows["deadbeefcafebabe1234"]["channel"] == "web"


def test_usage_summary_non_session_rows_unchanged(config):
    """``by=model``/``by=day`` rows are not labeled (no session enrichment)."""
    _seed_usage(config)
    out = readers.usage_summary(config, window="7d", by="model")
    assert "label" not in out["rows"][0]
    assert "channel" not in out["rows"][0]


# --- portfolio_overview ----------------------------------------------------


def _seed_portfolio(config):
    path = config.portfolio_db
    pdb.init_db(path)
    engine.add_asset(path, {"class": "equity", "label": "Acme",
                            "value": 1000, "cost_basis": 600,
                            "acquired": "2020-01-01"})
    engine.add_asset(path, {"class": "cash", "label": "Checking", "value": 500})
    engine.add_asset(path, {"class": "real_estate", "label": "House",
                            "value": 400000})
    engine.add_liability(path, {"class": "mortgage", "label": "House Mtg",
                                "balance": 250000, "linked_asset": "house"})
    return path


def test_portfolio_overview_matches_engine(config):
    path = _seed_portfolio(config)
    out = readers.portfolio_overview(config)
    assert out["available"] is True
    assert out["networth"] == engine.networth(path)
    assert out["rollup"] == engine.rollup(path)
    assert out["assets"] == engine.list_assets(path)


def test_portfolio_overview_missing_db(config):
    # No DB created.
    out = readers.portfolio_overview(config)
    assert out["available"] is False
    assert out["assets"] == []
    assert out["trades"] == []
    assert out["realized"] is None
    assert out["unrealized"] is None
    assert out["as_of"] is None


def test_portfolio_overview_trades_and_unrealized(config):
    path = config.portfolio_db
    pdb.init_db(path)
    # A lot bought in 2024, partly sold this year and last year.
    lot = engine.record_buy(path, {"ticker": "AAA", "qty": 20, "price": 100,
                                   "trade_date": "2024-01-01"})["asset_id"]
    engine.record_sell(path, {"asset_id": lot, "qty": 5, "price": 150,
                              "trade_date": "2025-06-01"})   # prior FY
    engine.record_sell(path, {"asset_id": lot, "qty": 5, "price": 200,
                              "trade_date": f"{datetime.now().year}-03-01"})  # this FY

    out = readers.portfolio_overview(config)
    assert out["available"] is True
    # unrealized mirrors the engine exactly.
    assert out["unrealized"] == engine.unrealized(path)
    # realized cards are this calendar year only (the prior-year sell excluded).
    yr = datetime.now().year
    assert out["realized"] == engine.realized_pnl(path, year=yr)
    # trades list is FY-to-date (newest-first), same window as realized.
    assert out["trades"] == engine.trade_history(path, since=f"{yr}-01-01")
    assert all(str(t["trade_date"]).startswith(str(yr)) for t in out["trades"])
    # as_of spans the holdings' value_asof dates.
    asof = out["as_of"]
    assert asof is not None and asof["oldest"] <= asof["newest"]


def test_portfolio_overview_year_override(config):
    path = config.portfolio_db
    pdb.init_db(path)
    lot = engine.record_buy(path, {"ticker": "AAA", "qty": 10, "price": 100,
                                   "trade_date": "2024-01-01"})["asset_id"]
    engine.record_sell(path, {"asset_id": lot, "qty": 5, "price": 150,
                              "trade_date": "2025-06-01"})
    out = readers.portfolio_overview(config, year=2025)
    assert out["realized"]["total"] == 250
    assert out["trades"] == engine.trade_history(path, since="2025-01-01")


# --- schedules -------------------------------------------------------------


def test_schedules_lists_tasks_with_next_fire(config):
    sdb.init_db(str(config.schedules_db))
    sengine.create(str(config.schedules_db),
                   {"id": "daily", "cron": "0 7 * * *", "prompt": "go", "enabled": True})
    sengine.create(str(config.schedules_db),
                   {"id": "off", "cron": "0 8 * * *", "prompt": "no", "enabled": False})
    out = readers.schedules(config)
    assert len(out) == 2
    daily = next(t for t in out if t["id"] == "daily")
    assert daily["enabled"] is True
    assert daily["next_fire"]  # ISO string, computed via croniter
    off = next(t for t in out if t["id"] == "off")
    assert off["enabled"] is False


def test_schedules_empty_when_no_file(config):
    assert readers.schedules(config) == []


# --- memory tree / file ----------------------------------------------------


def _seed_memory(config):
    mem = config.context_dir / "memory"
    (mem / "profile.md").write_text("# Profile\nfacts")
    (mem / "summaries").mkdir()
    (mem / "summaries" / "timeline.md").write_text("# Timeline")


def test_memory_tree_walks_directory(config):
    _seed_memory(config)
    tree = readers.memory_tree(config)
    # Top level has profile.md (file) and summaries (dir)
    names = {n["name"] for n in tree}
    assert "profile.md" in names
    assert "summaries" in names
    summaries = next(n for n in tree if n["name"] == "summaries")
    assert summaries["type"] == "dir"
    child_names = {c["name"] for c in summaries["children"]}
    assert "timeline.md" in child_names


def test_memory_file_returns_raw_markdown(config):
    _seed_memory(config)
    out = readers.memory_file(config, "profile.md")
    assert out["content"] == "# Profile\nfacts"
    assert out["relpath"] == "profile.md"


def test_memory_file_nested(config):
    _seed_memory(config)
    out = readers.memory_file(config, "summaries/timeline.md")
    assert out["content"] == "# Timeline"


def test_memory_file_rejects_traversal(config):
    _seed_memory(config)
    (config.context_dir / "identity.md").write_text("secret identity")
    with pytest.raises(ValueError):
        readers.memory_file(config, "../identity.md")


def test_memory_file_rejects_absolute(config):
    _seed_memory(config)
    with pytest.raises(ValueError):
        readers.memory_file(config, "/etc/passwd")


def test_memory_file_missing(config):
    _seed_memory(config)
    with pytest.raises(FileNotFoundError):
        readers.memory_file(config, "nope.md")
