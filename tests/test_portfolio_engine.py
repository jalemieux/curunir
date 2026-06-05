import sqlite3
from src.portfolio import db as pdb
from src.portfolio import engine


def _fresh(tmp_path):
    path = str(tmp_path / "portfolio.db")
    pdb.init_db(path)
    return path


def test_init_db_creates_tables_and_views(tmp_path):
    path = str(tmp_path / "portfolio.db")
    pdb.init_db(path)
    con = sqlite3.connect(path)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    con.close()
    assert {"assets", "liabilities",
            "v_networth", "v_rollup_by_class", "v_collectibles_pnl"} <= names


def test_readonly_connection_rejects_writes(tmp_path):
    path = str(tmp_path / "portfolio.db")
    pdb.init_db(path)
    con = pdb.connect(path, readonly=True)
    import pytest
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO assets(id,class,label,value) VALUES('x','cash','x',1)")
    con.close()


def test_add_asset_assigns_id_and_persists(tmp_path):
    path = _fresh(tmp_path)
    res = engine.add_asset(path, {"class": "cash", "label": "Checking", "value": 1000})
    assert res["id"]
    rows = engine.list_assets(path)
    assert len(rows) == 1 and rows[0]["label"] == "Checking"


def test_add_asset_requires_class_label_value(tmp_path):
    path = _fresh(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        engine.add_asset(path, {"class": "cash", "label": "x"})


def test_add_asset_warns_on_missing_basis_and_date(tmp_path):
    path = _fresh(tmp_path)
    res = engine.add_asset(path, {"class": "collectible", "label": "Watch A", "value": 5000})
    assert any("cost_basis" in w for w in res["warnings"])
    assert any("acquired" in w for w in res["warnings"])


def test_add_asset_warns_on_near_duplicate_label(tmp_path):
    path = _fresh(tmp_path)
    engine.add_asset(path, {"class": "collectible", "label": "Rolex GMT Batman",
                            "value": 15839, "cost_basis": 12000, "acquired": "2022-01-01"})
    res = engine.add_asset(path, {"class": "collectible", "label": "Rolex GMT Batman v2",
                                  "value": 16744, "cost_basis": 12500, "acquired": "2022-06-01"})
    assert any("similar" in w.lower() for w in res["warnings"])
