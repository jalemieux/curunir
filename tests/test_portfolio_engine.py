import sqlite3
from src.portfolio import db as pdb


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
