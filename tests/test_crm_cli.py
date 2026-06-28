import json
import subprocess
import sys


def _run(tmp_path, *args):
    db = str(tmp_path / "crm.db")
    proc = subprocess.run(
        [sys.executable, "skills/crm/crm.py", "--db", db, *args],
        capture_output=True, text=True)
    return proc.returncode, proc.stdout


def test_cli_add_then_pipeline(tmp_path):
    rc, out = _run(tmp_path, "add", "--name", "Jane", "--email", "jane@x.com")
    assert rc == 0 and json.loads(out)["id"]
    rc, out = _run(tmp_path, "pipeline")
    assert rc == 0
    pipe = json.loads(out)
    assert pipe["total"] == 1 and pipe["by_stage"]["new"] == 1


def test_cli_add_requires_name_errors(tmp_path):
    rc, out = _run(tmp_path, "add", "--company", "Acme")
    assert rc == 1 and "error" in json.loads(out)


def test_cli_set_stage_and_activity(tmp_path):
    rc, out = _run(tmp_path, "add", "--name", "Jane")
    lid = json.loads(out)["id"]
    rc, out = _run(tmp_path, "set-stage", lid, "qualified")
    assert rc == 0 and json.loads(out)["stage"] == "qualified"
    rc, out = _run(tmp_path, "activity", "--lead-id", lid)
    assert rc == 0
    acts = json.loads(out)
    assert len(acts) == 1 and acts[0]["kind"] == "stage_change"


def test_cli_set_stage_invalid_errors(tmp_path):
    rc, out = _run(tmp_path, "add", "--name", "Jane")
    lid = json.loads(out)["id"]
    rc, out = _run(tmp_path, "set-stage", lid, "bogus")
    assert rc == 1 and "error" in json.loads(out)


def test_cli_list_filter(tmp_path):
    _run(tmp_path, "add", "--name", "A", "--email", "a@x.com", "--source", "beta-signup")
    _run(tmp_path, "add", "--name", "B", "--email", "b@x.com", "--source", "referral")
    rc, out = _run(tmp_path, "list", "--source", "beta-signup")
    assert rc == 0 and len(json.loads(out)) == 1


def test_cli_show(tmp_path):
    rc, out = _run(tmp_path, "add", "--name", "Jane", "--company", "Acme")
    lid = json.loads(out)["id"]
    rc, out = _run(tmp_path, "show", lid)
    assert rc == 0 and json.loads(out)["company"] == "Acme"


def test_cli_log_interaction(tmp_path):
    rc, out = _run(tmp_path, "add", "--name", "Jane")
    lid = json.loads(out)["id"]
    rc, out = _run(tmp_path, "log", lid, "--kind", "email", "--body", "hello")
    assert rc == 0 and json.loads(out)["kind"] == "email"


def test_cli_import_rows_from_file(tmp_path):
    rows = [{"name": "X", "email": "x@x.com"}, {"name": "Y", "email": "y@x.com"}]
    f = tmp_path / "rows.json"
    f.write_text(json.dumps(rows))
    rc, out = _run(tmp_path, "import-rows", "--rows-file", str(f),
                   "--source", "beta-signup")
    assert rc == 0 and json.loads(out)["imported"] == 2


def test_cli_query(tmp_path):
    _run(tmp_path, "add", "--name", "Jane")
    rc, out = _run(tmp_path, "query", "SELECT name FROM leads")
    assert rc == 0 and json.loads(out) == [{"name": "Jane"}]


def test_cli_render(tmp_path):
    _run(tmp_path, "add", "--name", "Jane")
    rc, out = _run(tmp_path, "render")
    assert rc == 0 and "Pipeline" in json.loads(out)["markdown"]


def test_cli_rm(tmp_path):
    rc, out = _run(tmp_path, "add", "--name", "Jane")
    lid = json.loads(out)["id"]
    rc, out = _run(tmp_path, "rm", lid)
    assert rc == 0 and json.loads(out)["removed"] == lid
