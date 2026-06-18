"""CLI tests: subprocess invocation, JSON stdout, and --from-portfolio reading
a seeded portfolio DB for the starting balance."""
import json
import subprocess
import sys

from src.portfolio import db as pdb
from src.portfolio import engine as pengine

_COMMON = ["--current-age", "50", "--retirement-age", "65", "--end-age", "90",
           "--annual-contribution", "20000", "--annual-spending", "60000"]


def _run(*args):
    proc = subprocess.run(
        [sys.executable, "skills/financial-plan/plan.py", *args],
        capture_output=True, text=True)
    return proc.returncode, proc.stdout


def test_cli_project_json():
    rc, out = _run("project", "--current-balance", "500000", *_COMMON)
    assert rc == 0
    data = json.loads(out)
    assert data["rows"][0]["age"] == 50


def test_cli_montecarlo_seeded_json():
    rc, out = _run("montecarlo", "--current-balance", "500000",
                   "--seed", "5", "--n-sims", "200", *_COMMON)
    assert rc == 0
    assert "success_prob" in json.loads(out)


def test_cli_stress_json():
    rc, out = _run("stress", "--current-balance", "500000",
                   "--seed", "1", "--n-sims", "100", *_COMMON)
    assert rc == 0
    labels = {s["scenario"] for s in json.loads(out)["scenarios"]}
    assert "ltc_late_life" in labels


def test_cli_bad_input_errors_as_json():
    rc, out = _run("project", "--current-age", "70", "--retirement-age", "65",
                   "--end-age", "60", "--current-balance", "1", "--annual-spending", "1")
    assert rc == 1 and "error" in json.loads(out)


def test_cli_from_portfolio_pulls_networth(tmp_path):
    db = str(tmp_path / "portfolio.db")
    pdb.init_db(db)
    pengine.add_asset(db, {"class": "cash", "label": "Cash", "value": 750000})
    rc, out = _run("project", "--from-portfolio", "--db", db, *_COMMON)
    assert rc == 0
    data = json.loads(out)
    # Starting balance came from the portfolio net worth (750k), not a flag.
    assert data["rows"][0]["start_balance"] == 750000.0
