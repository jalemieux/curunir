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


def test_cli_add_liability_then_networth(tmp_path):
    _run(tmp_path, "add", "--class", "real_estate", "--label", "House", "--value", "1000000")
    rc, _ = _run(tmp_path, "add-liability", "--class", "mortgage", "--label", "Mtg", "--balance", "400000")
    assert rc == 0
    rc, out = _run(tmp_path, "networth")
    assert rc == 0 and json.loads(out)["net_worth"] == 600000


def test_cli_import_rows_from_file(tmp_path):
    rows = [{"class": "equity", "label": "VOO", "value": 7000},
            {"class": "equity", "label": "GLD", "value": 2200}]
    f = tmp_path / "rows.json"
    f.write_text(json.dumps(rows))
    rc, out = _run(tmp_path, "import-rows", "--rows-file", str(f),
                   "--account", "brk", "--stated-total", "9200")
    assert rc == 0 and json.loads(out)["self_check"]["ok"] is True
