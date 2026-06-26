"""Snapshot subsystem (append-only point-in-time time-series) — engine tests."""
import sqlite3

import pytest

from src.portfolio import db as pdb
from src.portfolio import engine


def _fresh(tmp_path):
    path = str(tmp_path / "portfolio.db")
    pdb.init_db(path)
    return path


# --- schema -----------------------------------------------------------------

def test_init_db_creates_snapshot_tables_and_view(tmp_path):
    path = _fresh(tmp_path)
    con = sqlite3.connect(path)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    con.close()
    assert {"snapshots", "snapshot_assets", "snapshot_liabilities",
            "v_snapshot_networth"} <= names


def test_init_db_idempotent_adds_snapshot_tables_without_touching_rows(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 100})
    pdb.init_db(path)  # run again on a populated DB
    con = sqlite3.connect(path)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {"snapshots", "snapshot_assets", "snapshot_liabilities"} <= names
    assert engine.list_assets(path)[0]["value"] == 100


# --- snapshot() -------------------------------------------------------------

def test_snapshot_records_current_totals(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "equity", "label": "VOO", "value": 7000})
    engine.add_asset(path, {"class": "real_estate", "label": "House", "value": 1000000})
    engine.add_liability(path, {"class": "mortgage", "label": "mtg", "balance": 400000})
    snap = engine.snapshot(path, taken_at="2026-01-01T09:00:00")
    assert snap["total_assets"] == 1007000
    assert snap["total_liabilities"] == 400000
    assert snap["net_worth"] == 607000
    assert snap["n_assets"] == 2
    assert snap["n_liabilities"] == 1
    assert snap["trigger"] == "manual"
    assert snap["id"].startswith("snap-")


def test_snapshot_freezes_state_against_later_refresh(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "equity", "label": "VOO", "ticker": "VOO",
                            "qty": 10, "value": 7000})
    snap = engine.snapshot(path, taken_at="2026-01-01T09:00:00")
    engine.refresh(path, quoter=lambda t: 800.0)  # VOO -> 8000 live
    shown = engine.show_snapshot(path, snap["id"])
    assert shown["assets"][0]["value"] == 7000      # frozen, not 8000
    assert shown["snapshot"]["net_worth"] == 7000


def test_snapshot_survives_asset_deletion(tmp_path):
    path = _fresh(tmp_path)
    aid = engine.add_asset(path, {"class": "equity", "label": "VOO", "value": 7000})["id"]
    snap = engine.snapshot(path, taken_at="2026-01-01T09:00:00")
    engine.remove_asset(path, aid)
    shown = engine.show_snapshot(path, snap["id"])
    assert len(shown["assets"]) == 1
    assert shown["assets"][0]["label"] == "VOO"


def test_snapshot_dedup_same_date_and_trigger_warns(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 100})
    engine.snapshot(path, taken_at="2026-01-01T09:00:00")
    dup = engine.snapshot(path, taken_at="2026-01-01T15:00:00")
    assert "warning" in dup
    assert dup["existing"]["id"]
    assert len(engine.list_snapshots(path)) == 1


def test_snapshot_force_inserts_duplicate(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 100})
    engine.snapshot(path, taken_at="2026-01-01T09:00:00")
    forced = engine.snapshot(path, taken_at="2026-01-01T16:00:00", force=True)
    assert "id" in forced
    assert len(engine.list_snapshots(path)) == 2


def test_snapshot_different_trigger_not_deduped(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 100})
    engine.snapshot(path, taken_at="2026-01-01T09:00:00", trigger="manual")
    engine.snapshot(path, taken_at="2026-01-01T10:00:00", trigger="refresh")
    assert len(engine.list_snapshots(path)) == 2


def test_snapshot_records_note(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 100})
    snap = engine.snapshot(path, taken_at="2026-01-01T09:00:00", note="year start")
    assert engine.show_snapshot(path, snap["id"])["snapshot"]["note"] == "year start"


# --- list_snapshots() -------------------------------------------------------

def test_list_snapshots_newest_first(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 100})
    engine.snapshot(path, taken_at="2026-01-01T09:00:00")
    engine.snapshot(path, taken_at="2026-02-01T09:00:00")
    engine.snapshot(path, taken_at="2026-03-01T09:00:00")
    lst = engine.list_snapshots(path)
    assert [s["taken_at"][:7] for s in lst] == ["2026-03", "2026-02", "2026-01"]


def test_list_snapshots_date_range_filter(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 100})
    engine.snapshot(path, taken_at="2026-01-01T09:00:00")
    engine.snapshot(path, taken_at="2026-02-15T09:00:00")
    engine.snapshot(path, taken_at="2026-03-01T09:00:00")
    lst = engine.list_snapshots(path, since="2026-02-01", until="2026-02-28")
    assert len(lst) == 1
    assert lst[0]["taken_at"][:7] == "2026-02"


# --- show_snapshot() --------------------------------------------------------

def test_show_snapshot_by_id_date_and_latest(tmp_path):
    path = _fresh(tmp_path)
    aid = engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 100})["id"]
    s1 = engine.snapshot(path, taken_at="2026-01-01T09:00:00")
    engine.update_asset(path, aid, {"value": 200})
    s2 = engine.snapshot(path, taken_at="2026-02-01T09:00:00")
    assert engine.show_snapshot(path, s1["id"])["snapshot"]["net_worth"] == 100
    assert engine.show_snapshot(path, "2026-01-01")["snapshot"]["net_worth"] == 100
    assert engine.show_snapshot(path, "latest")["snapshot"]["net_worth"] == 200
    assert engine.show_snapshot(path, s2["id"])["snapshot"]["net_worth"] == 200


def test_show_snapshot_unknown_raises(tmp_path):
    path = _fresh(tmp_path)
    with pytest.raises(KeyError):
        engine.show_snapshot(path, "nope")


def test_show_snapshot_includes_liabilities(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "real_estate", "label": "House", "value": 1000000})
    engine.add_liability(path, {"class": "mortgage", "label": "mtg", "balance": 400000})
    snap = engine.snapshot(path, taken_at="2026-01-01T09:00:00")
    shown = engine.show_snapshot(path, snap["id"])
    assert len(shown["liabilities"]) == 1
    assert shown["liabilities"][0]["balance"] == 400000


# --- diff_snapshots() -------------------------------------------------------

def test_diff_gain_loss_new_closed(tmp_path):
    path = _fresh(tmp_path)
    voo = engine.add_asset(path, {"class": "equity", "label": "VOO", "value": 7000})["id"]
    gld = engine.add_asset(path, {"class": "equity", "label": "GLD", "value": 2000})["id"]
    a = engine.snapshot(path, taken_at="2026-01-01T09:00:00")
    engine.update_asset(path, voo, {"value": 8000})   # gained +1000
    engine.remove_asset(path, gld)                    # closed
    engine.add_asset(path, {"class": "equity", "label": "AAPL", "value": 1500})  # new
    b = engine.snapshot(path, taken_at="2026-02-01T09:00:00")

    d = engine.diff_snapshots(path, a["id"], b["id"])
    # A net 9000, B net 9500
    assert d["net_worth"]["a"] == 9000
    assert d["net_worth"]["b"] == 9500
    assert d["net_worth"]["abs"] == 500
    by_label = {r["label"]: r for r in d["assets"]}
    assert by_label["VOO"]["status"] == "gained" and by_label["VOO"]["delta"] == 1000
    assert by_label["GLD"]["status"] == "closed"
    assert by_label["AAPL"]["status"] == "new"


def test_diff_falls_back_to_class_label_ticker(tmp_path):
    path = _fresh(tmp_path)
    voo = engine.add_asset(path, {"class": "equity", "label": "VOO",
                                  "ticker": "VOO", "value": 7000})["id"]
    a = engine.snapshot(path, taken_at="2026-01-01T09:00:00")
    engine.remove_asset(path, voo)
    # reopened under a different asset_id, same (class,label,ticker)
    engine.add_asset(path, {"id": "voo-reopened", "class": "equity",
                            "label": "VOO", "ticker": "VOO", "value": 9000})
    b = engine.snapshot(path, taken_at="2026-02-01T09:00:00")

    d = engine.diff_snapshots(path, a["id"], b["id"])
    voo_rows = [r for r in d["assets"] if r["label"] == "VOO"]
    assert len(voo_rows) == 1            # matched, not split into closed + new
    assert voo_rows[0]["status"] == "gained"
    assert voo_rows[0]["delta"] == 2000


def test_diff_accepts_latest_alias(tmp_path):
    path = _fresh(tmp_path)
    aid = engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 100})["id"]
    a = engine.snapshot(path, taken_at="2026-01-01T09:00:00")
    engine.update_asset(path, aid, {"value": 250})
    engine.snapshot(path, taken_at="2026-02-01T09:00:00")
    d = engine.diff_snapshots(path, a["id"], "latest")
    assert d["net_worth"]["abs"] == 150


# --- refresh integration ----------------------------------------------------

def test_refresh_snapshot_before_captures_prerefresh_state(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "equity", "label": "VOO", "ticker": "VOO",
                            "qty": 10, "value": 7000})
    engine.refresh(path, quoter=lambda t: 800.0, snapshot_before=True)
    snaps = engine.list_snapshots(path)
    assert len(snaps) == 1
    assert snaps[0]["trigger"] == "refresh"
    shown = engine.show_snapshot(path, snaps[0]["id"])
    assert shown["assets"][0]["value"] == 7000     # pre-refresh frozen
    assert engine.show(path, "voo")["value"] == 8000   # live repriced


def test_refresh_default_writes_no_snapshot(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "equity", "label": "VOO", "ticker": "VOO",
                            "qty": 10, "value": 7000})
    engine.refresh(path, quoter=lambda t: 800.0)
    assert engine.list_snapshots(path) == []


# --- net-worth time-series view ---------------------------------------------

def test_v_snapshot_networth_is_queryable(tmp_path):
    path = _fresh(tmp_path)
    aid = engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 100})["id"]
    engine.snapshot(path, taken_at="2026-01-01T09:00:00")
    engine.update_asset(path, aid, {"value": 300})
    engine.snapshot(path, taken_at="2026-02-01T09:00:00")
    rows = engine.query(path, "SELECT net_worth FROM v_snapshot_networth ORDER BY taken_at")
    assert [r["net_worth"] for r in rows] == [100, 300]


# --- markdown renderers -----------------------------------------------------

def test_render_snapshot_list_is_markdown_table(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 100})
    engine.snapshot(path, taken_at="2026-01-01T09:00:00")
    md = engine.render_snapshot_list(path)
    assert "|" in md and "net worth" in md.lower()


def test_render_snapshot_diff_is_markdown(tmp_path):
    path = _fresh(tmp_path)
    aid = engine.add_asset(path, {"class": "cash", "label": "Cash", "value": 100})["id"]
    a = engine.snapshot(path, taken_at="2026-01-01T09:00:00")
    engine.update_asset(path, aid, {"value": 250})
    b = engine.snapshot(path, taken_at="2026-02-01T09:00:00")
    md = engine.render_snapshot_diff(path, a["id"], b["id"])
    assert "|" in md and "Net Worth" in md
