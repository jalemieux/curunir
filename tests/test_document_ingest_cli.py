"""Tests for the document-ingest skill CLI (skills/document-ingest/ingest.py).

The CLI is a thin adapter over src.document_ingest.ingest_document. We load it
by path (it lives under skills/, not an importable package) and patch
ingest_document so nothing hits an LLM.
"""
import importlib.util
from pathlib import Path

from src.document_ingest import DocumentIngestError

ROOT = Path(__file__).resolve().parents[1]


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "document_ingest_cli", ROOT / "skills" / "document-ingest" / "ingest.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cli = _load_cli()


def test_cli_prints_card_and_exits_zero(tmp_path, capsys, monkeypatch):
    async def fake_ingest(path, config, usage_store=None):
        assert Path(path).name == "doc.txt"
        return "# Document card: doc.txt"

    monkeypatch.setattr(cli, "ingest_document", fake_ingest)
    rc = cli.main(["doc.txt", "--usage-db", str(tmp_path / "usage.db")])

    assert rc == 0
    out = capsys.readouterr().out
    assert "# Document card: doc.txt" in out


def test_cli_error_goes_to_stderr_and_exits_one(tmp_path, capsys, monkeypatch):
    async def fake_ingest(path, config, usage_store=None):
        raise DocumentIngestError("Document not found: doc.txt")

    monkeypatch.setattr(cli, "ingest_document", fake_ingest)
    rc = cli.main(["doc.txt", "--usage-db", str(tmp_path / "usage.db")])

    assert rc == 1
    captured = capsys.readouterr()
    assert "Document not found" in captured.err
    assert captured.out == ""


def test_cli_wires_usage_store(tmp_path, monkeypatch):
    seen = {}

    async def fake_ingest(path, config, usage_store=None):
        seen["store_db"] = usage_store.db_path if usage_store else None
        return "card"

    monkeypatch.setattr(cli, "ingest_document", fake_ingest)
    rc = cli.main(["doc.txt", "--usage-db", str(tmp_path / "usage.db")])

    assert rc == 0
    assert seen["store_db"] == tmp_path / "usage.db"
