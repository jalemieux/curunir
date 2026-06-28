import json
import subprocess
import sys

from src.config import AgentConfig
from src.tools.crm_tool import exec_crm


def _cfg(tmp_path):
    cfg = AgentConfig()
    cfg.crm_db = str(tmp_path / "crm.db")
    return cfg


def test_tool_add_then_pipeline(tmp_path):
    cfg = _cfg(tmp_path)
    exec_crm({"action": "add",
              "args": {"name": "Jane", "email": "jane@x.com"}}, cfg)
    out = json.loads(exec_crm({"action": "pipeline"}, cfg))
    assert out["total"] == 1
    assert out["by_stage"]["new"] == 1


def test_tool_set_stage_and_activity(tmp_path):
    cfg = _cfg(tmp_path)
    add = json.loads(exec_crm({"action": "add", "args": {"name": "Jane"}}, cfg))
    lid = add["id"]
    res = json.loads(exec_crm({"action": "set_stage",
                               "args": {"id": lid, "stage": "qualified"}}, cfg))
    assert res["stage"] == "qualified"
    acts = json.loads(exec_crm({"action": "activity", "args": {"lead_id": lid}}, cfg))
    assert len(acts) == 1 and acts[0]["kind"] == "stage_change"


def test_tool_list_filter(tmp_path):
    cfg = _cfg(tmp_path)
    exec_crm({"action": "add", "args": {"name": "A", "email": "a@x.com",
                                        "source": "beta-signup"}}, cfg)
    exec_crm({"action": "add", "args": {"name": "B", "email": "b@x.com",
                                        "source": "referral"}}, cfg)
    out = json.loads(exec_crm({"action": "list",
                               "args": {"source": "beta-signup"}}, cfg))
    assert len(out) == 1 and out[0]["name"] == "A"


def test_tool_log_and_import_rows(tmp_path):
    cfg = _cfg(tmp_path)
    add = json.loads(exec_crm({"action": "add", "args": {"name": "Jane"}}, cfg))
    log = json.loads(exec_crm({"action": "log", "args": {
        "lead_id": add["id"], "kind": "email", "body": "hi"}}, cfg))
    assert log["kind"] == "email"
    imp = json.loads(exec_crm({"action": "import_rows", "args": {
        "rows": [{"name": "X", "email": "x@x.com"},
                 {"name": "Y", "email": "y@x.com"}],
        "source": "beta-signup"}}, cfg))
    assert imp["imported"] == 2


def test_tool_unknown_action_errors(tmp_path):
    cfg = _cfg(tmp_path)
    out = json.loads(exec_crm({"action": "frobnicate"}, cfg))
    assert "error" in out and "hint" in out


def test_tool_bad_args_returns_error_json(tmp_path):
    cfg = _cfg(tmp_path)
    # show with a missing id surfaces as JSON, never a traceback.
    out = json.loads(exec_crm({"action": "show", "args": {"id": "nope"}}, cfg))
    assert "error" in out


def test_tool_every_read_action_returns_valid_json(tmp_path):
    cfg = _cfg(tmp_path)
    exec_crm({"action": "add", "args": {"name": "Jane", "email": "jane@x.com"}}, cfg)
    for action in ("list", "pipeline", "render"):
        json.loads(exec_crm({"action": action}, cfg))  # must not raise
    json.loads(exec_crm({"action": "query",
                         "args": {"sql": "SELECT name FROM leads"}}, cfg))


def _cli(tmp_path, *args):
    db = str(tmp_path / "crm.db")
    proc = subprocess.run(
        [sys.executable, "skills/crm/crm.py", "--db", db, *args],
        capture_output=True, text=True)
    return proc.returncode, proc.stdout


def test_tool_and_cli_identical_for_same_op(tmp_path):
    # Acceptance criterion: the tool and CLI front the same engine and produce
    # identical results for the same operation.
    cfg = _cfg(tmp_path / "tool")
    (tmp_path / "tool").mkdir()
    cli_dir = tmp_path / "cli"
    cli_dir.mkdir()

    exec_crm({"action": "add", "args": {"name": "Jane Doe", "company": "Acme",
                                        "stage": "qualified"}}, cfg)
    _cli(cli_dir, "add", "--name", "Jane Doe", "--company", "Acme",
         "--stage", "qualified")

    tool_pipe = json.loads(exec_crm({"action": "pipeline"}, cfg))
    _, cli_out = _cli(cli_dir, "pipeline")
    assert tool_pipe == json.loads(cli_out)
