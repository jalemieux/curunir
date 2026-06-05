import json
import subprocess
import sys


def _run(tmp_path, *args):
    db = str(tmp_path / "portfolio.db")
    proc = subprocess.run(
        [sys.executable, "skills/balance-sheet/portfolio.py", "--db", db, *args],
        capture_output=True, text=True)
    return proc.returncode, proc.stdout


def test_cli_add_then_networth(tmp_path):
    rc, _ = _run(tmp_path, "add", "--class", "cash", "--label", "Cash", "--value", "1000")
    assert rc == 0
    rc, out = _run(tmp_path, "networth")
    assert rc == 0 and json.loads(out)["net_worth"] == 1000


def test_cli_unknown_class_errors(tmp_path):
    rc, out = _run(tmp_path, "add", "--class", "bogus", "--label", "X", "--value", "1")
    assert rc == 1 and "error" in json.loads(out)
