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


def test_cli_buy_sell_trades_realized(tmp_path):
    rc, out = _run(tmp_path, "buy", "--ticker", "SPCX", "--qty", "100",
                   "--price", "150", "--trade-date", "2024-01-10")
    assert rc == 0
    lot = json.loads(out)["asset_id"]
    rc, out = _run(tmp_path, "sell", lot, "--qty", "40", "--price", "170",
                   "--trade-date", "2026-06-13")
    assert rc == 0 and json.loads(out)["realized_pnl"] == 800
    rc, out = _run(tmp_path, "trades", "--ticker", "SPCX")
    assert rc == 0 and len(json.loads(out)) == 2
    rc, out = _run(tmp_path, "realized", "--year", "2026")
    assert rc == 0 and json.loads(out)["total"] == 800


def test_cli_sell_oversell_errors(tmp_path):
    rc, out = _run(tmp_path, "buy", "--ticker", "VOO", "--qty", "5",
                   "--price", "500", "--trade-date", "2026-01-01")
    lot = json.loads(out)["asset_id"]
    rc, out = _run(tmp_path, "sell", lot, "--qty", "6", "--price", "510",
                   "--trade-date", "2026-02-01")
    assert rc == 1 and "error" in json.loads(out)
