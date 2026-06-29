import sqlite3

import pytest

from src.crm import db as cdb
from src.crm import engine


def _fresh(tmp_path):
    path = str(tmp_path / "crm.db")
    cdb.init_db(path)
    return path


def test_init_db_creates_tables_and_views(tmp_path):
    path = str(tmp_path / "crm.db")
    cdb.init_db(path)
    con = sqlite3.connect(path)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    con.close()
    assert {"leads", "interactions",
            "v_pipeline_by_stage", "v_lead_latest_activity"} <= names


def test_init_db_idempotent_on_existing(tmp_path):
    path = _fresh(tmp_path)
    engine.add_lead(path, {"name": "Jane"})
    cdb.init_db(path)  # second init must not wipe or error
    assert len(engine.list_leads(path)) == 1


def test_readonly_connection_rejects_writes(tmp_path):
    path = _fresh(tmp_path)
    con = cdb.connect(path, readonly=True)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO leads(id,name,stage) VALUES('x','x','new')")
    con.close()


def test_add_lead_assigns_id_defaults_stage_and_persists(tmp_path):
    path = _fresh(tmp_path)
    res = engine.add_lead(path, {"name": "Jane Doe", "company": "Acme"})
    assert res["id"]
    rows = engine.list_leads(path)
    assert len(rows) == 1
    assert rows[0]["name"] == "Jane Doe"
    assert rows[0]["stage"] == "new"          # default stage
    assert rows[0]["created_at"] and rows[0]["updated_at"]


def test_add_lead_requires_name(tmp_path):
    path = _fresh(tmp_path)
    with pytest.raises(ValueError):
        engine.add_lead(path, {"company": "Acme"})


def test_add_lead_rejects_unknown_stage(tmp_path):
    path = _fresh(tmp_path)
    with pytest.raises(ValueError):
        engine.add_lead(path, {"name": "Jane", "stage": "bogus"})


def test_add_lead_slug_id_from_name(tmp_path):
    path = _fresh(tmp_path)
    res = engine.add_lead(path, {"name": "Jane Doe"})
    assert res["id"] == "jane-doe"


def test_add_lead_dedupes_slug_collision(tmp_path):
    path = _fresh(tmp_path)
    a = engine.add_lead(path, {"name": "Jane Doe", "email": "a@x.com"})["id"]
    b = engine.add_lead(path, {"name": "Jane Doe", "email": "b@x.com"})["id"]
    assert a == "jane-doe" and b == "jane-doe-2"


def test_add_lead_exact_email_duplicate_raises(tmp_path):
    path = _fresh(tmp_path)
    engine.add_lead(path, {"name": "Jane", "email": "jane@acme.com"})
    with pytest.raises(ValueError):
        engine.add_lead(path, {"name": "Jane Two", "email": "JANE@acme.com"})


def test_add_lead_near_duplicate_name_warns(tmp_path):
    path = _fresh(tmp_path)
    engine.add_lead(path, {"name": "Jane Doe", "email": "a@x.com"})
    res = engine.add_lead(path, {"name": "Jane Doe Jr", "email": "b@x.com"})
    assert any("similar" in w.lower() for w in res["warnings"])


def test_add_lead_extra_round_trips_json(tmp_path):
    path = _fresh(tmp_path)
    aid = engine.add_lead(path, {"name": "Jane",
                                 "extra": {"utm": "twitter", "score": 7}})["id"]
    import json
    stored = engine.show(path, aid)["extra"]
    assert json.loads(stored) == {"utm": "twitter", "score": 7}


def test_add_lead_with_source_beta_signup(tmp_path):
    # The driving ingestion use case: a single add_lead call.
    path = _fresh(tmp_path)
    engine.add_lead(path, {"name": "Beta User", "email": "beta@x.com",
                           "source": "beta-signup"})
    assert engine.list_leads(path, source="beta-signup")[0]["name"] == "Beta User"


def test_update_lead_sets_fields_and_stamps_updated(tmp_path):
    path = _fresh(tmp_path)
    aid = engine.add_lead(path, {"name": "Jane"})["id"]
    before = engine.show(path, aid)["updated_at"]
    engine.update_lead(path, aid, {"company": "NewCo"})
    after = engine.show(path, aid)
    assert after["company"] == "NewCo"
    assert after["updated_at"] >= before


def test_update_lead_unknown_id_raises(tmp_path):
    path = _fresh(tmp_path)
    with pytest.raises(KeyError):
        engine.update_lead(path, "nope", {"company": "X"})


def test_set_stage_rejects_invalid(tmp_path):
    path = _fresh(tmp_path)
    aid = engine.add_lead(path, {"name": "Jane"})["id"]
    with pytest.raises(ValueError):
        engine.set_stage(path, aid, "bogus")


def test_set_stage_updates_and_logs_stage_change(tmp_path):
    path = _fresh(tmp_path)
    aid = engine.add_lead(path, {"name": "Jane"})["id"]
    res = engine.set_stage(path, aid, "qualified")
    assert res["stage"] == "qualified" and res["previous_stage"] == "new"
    assert engine.show(path, aid)["stage"] == "qualified"
    acts = engine.activity(path, lead_id=aid)
    assert len(acts) == 1
    assert acts[0]["kind"] == "stage_change"
    assert "new" in acts[0]["body"] and "qualified" in acts[0]["body"]


def test_set_stage_unknown_lead_raises(tmp_path):
    path = _fresh(tmp_path)
    with pytest.raises(KeyError):
        engine.set_stage(path, "nope", "qualified")


def test_log_interaction_requires_lead_and_kind(tmp_path):
    path = _fresh(tmp_path)
    with pytest.raises(ValueError):
        engine.log_interaction(path, {"lead_id": "x"})


def test_log_interaction_is_append_only_and_survives_remove(tmp_path):
    path = _fresh(tmp_path)
    aid = engine.add_lead(path, {"name": "Jane"})["id"]
    engine.log_interaction(path, {"lead_id": aid, "kind": "email",
                                  "body": "sent welcome"})
    engine.log_interaction(path, {"lead_id": aid, "kind": "call",
                                  "body": "discovery call"})
    assert len(engine.activity(path, lead_id=aid)) == 2
    engine.remove_lead(path, aid)
    assert engine.list_leads(path) == []
    # Ledger survives the lead deletion (soft ref).
    assert len(engine.activity(path, lead_id=aid)) == 2


def test_remove_lead_unknown_raises(tmp_path):
    path = _fresh(tmp_path)
    with pytest.raises(KeyError):
        engine.remove_lead(path, "nope")


def test_show_unknown_raises(tmp_path):
    path = _fresh(tmp_path)
    with pytest.raises(KeyError):
        engine.show(path, "nope")


def test_list_leads_filters(tmp_path):
    path = _fresh(tmp_path)
    engine.add_lead(path, {"name": "A", "email": "a@x.com", "source": "referral",
                           "owner": "sam", "stage": "new"})
    engine.add_lead(path, {"name": "B", "email": "b@x.com", "source": "beta-signup",
                           "owner": "lee", "stage": "qualified"})
    assert len(engine.list_leads(path, stage="new")) == 1
    assert len(engine.list_leads(path, source="beta-signup")) == 1
    assert len(engine.list_leads(path, owner="lee")) == 1
    assert len(engine.list_leads(path)) == 2


def test_pipeline_counts_by_stage(tmp_path):
    path = _fresh(tmp_path)
    engine.add_lead(path, {"name": "A", "email": "a@x.com"})          # new
    engine.add_lead(path, {"name": "B", "email": "b@x.com"})          # new
    c = engine.add_lead(path, {"name": "C", "email": "c@x.com"})["id"]
    engine.set_stage(path, c, "qualified")
    pipe = engine.pipeline(path)
    assert pipe["total"] == 3
    assert pipe["by_stage"]["new"] == 2
    assert pipe["by_stage"]["qualified"] == 1
    assert pipe["by_stage"]["won"] == 0          # zero-filled stable shape


def test_activity_filters_and_limits(tmp_path):
    path = _fresh(tmp_path)
    aid = engine.add_lead(path, {"name": "Jane"})["id"]
    engine.log_interaction(path, {"lead_id": aid, "kind": "note",
                                  "body": "n1", "occurred_at": "2026-01-01T09:00:00"})
    engine.log_interaction(path, {"lead_id": aid, "kind": "note",
                                  "body": "n2", "occurred_at": "2026-03-01T09:00:00"})
    assert len(engine.activity(path, since="2026-02-01")) == 1
    assert len(engine.activity(path, limit=1)) == 1
    # Newest-first.
    assert engine.activity(path)[0]["body"] == "n2"


def test_query_is_readonly(tmp_path):
    path = _fresh(tmp_path)
    engine.add_lead(path, {"name": "Jane"})
    rows = engine.query(path, "SELECT name, stage FROM leads")
    assert rows == [{"name": "Jane", "stage": "new"}]
    with pytest.raises(Exception):
        engine.query(path, "DELETE FROM leads")


def test_query_rejects_non_select(tmp_path):
    path = _fresh(tmp_path)
    with pytest.raises(ValueError):
        engine.query(path, "ATTACH DATABASE '/tmp/evil.db' AS evil")


def test_import_rows_inserts_and_stamps_source(tmp_path):
    path = _fresh(tmp_path)
    rows = [{"name": "A", "email": "a@x.com"},
            {"name": "B", "email": "b@x.com"}]
    res = engine.import_rows(path, rows, source="beta-signup", owner="sam")
    assert res["imported"] == 2
    leads = engine.list_leads(path, source="beta-signup")
    assert len(leads) == 2
    assert all(lead["owner"] == "sam" for lead in leads)


def test_import_rows_bad_stage_aborts_before_insert(tmp_path):
    path = _fresh(tmp_path)
    rows = [{"name": "A", "email": "a@x.com"},
            {"name": "B", "email": "b@x.com", "stage": "bogus"}]
    with pytest.raises(ValueError):
        engine.import_rows(path, rows)
    assert engine.list_leads(path) == []          # all-or-nothing on validation


def test_import_rows_duplicate_email_in_batch_aborts(tmp_path):
    path = _fresh(tmp_path)
    rows = [{"name": "A", "email": "dup@x.com"},
            {"name": "B", "email": "DUP@x.com"}]
    with pytest.raises(ValueError):
        engine.import_rows(path, rows)
    assert engine.list_leads(path) == []


def test_render_markdown_has_pipeline_and_warning(tmp_path):
    path = _fresh(tmp_path)
    engine.add_lead(path, {"name": "Jane", "company": "Acme"})
    md = engine.render_markdown(path)
    assert "do not hand-edit" in md.lower()
    assert "Pipeline" in md and "Jane" in md
