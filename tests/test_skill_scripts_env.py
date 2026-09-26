"""Skill scripts default their store paths from the dirs the bash tool exports.

``CURUNIR_CONTEXT_DIR`` / ``CURUNIR_SHARED_DIR`` are set by ``exec_bash`` for
the agent running the skill (agents-and-containers, phase 1). Without them a
human at a shell gets the legacy ``context/`` layout. The scripts live under
``skills/`` (not a package), so they are loaded by path.
"""
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_portfolio_default_db_follows_context_dir(monkeypatch, tmp_path):
    mod = _load("portfolio_cli_env", "skills/balance-sheet/portfolio.py")
    monkeypatch.delenv("CURUNIR_CONTEXT_DIR", raising=False)
    assert mod._default_db() == os.path.join("context", "memory", "portfolio.db")
    assert mod.build_parser().parse_args(["networth"]).db == mod._default_db()
    monkeypatch.setenv("CURUNIR_CONTEXT_DIR", str(tmp_path / "agents" / "fin"))
    expected = str(tmp_path / "agents" / "fin" / "memory" / "portfolio.db")
    assert mod._default_db() == expected
    # the parser reads the env at build time, not at import time
    assert mod.build_parser().parse_args(["networth"]).db == expected


def test_crm_default_db_follows_context_dir(monkeypatch, tmp_path):
    mod = _load("crm_cli_env", "skills/crm/crm.py")
    monkeypatch.delenv("CURUNIR_CONTEXT_DIR", raising=False)
    assert mod._default_db() == os.path.join("context", "memory", "crm.db")
    monkeypatch.setenv("CURUNIR_CONTEXT_DIR", str(tmp_path / "agents" / "mkt"))
    expected = str(tmp_path / "agents" / "mkt" / "memory" / "crm.db")
    assert mod.build_parser().parse_args(["pipeline"]).db == expected


def test_webcam_out_dir_follows_shared_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("CURUNIR_SHARED_DIR", raising=False)
    legacy = _load("webcam_cli_env_legacy", "skills/webcam/snapshot.py")
    assert legacy.DEFAULT_OUT_DIR == ROOT / "context" / "workspace" / "generated"
    monkeypatch.setenv("CURUNIR_SHARED_DIR", str(tmp_path))
    scoped = _load("webcam_cli_env_scoped", "skills/webcam/snapshot.py")
    assert scoped.DEFAULT_OUT_DIR == tmp_path / "workspace" / "generated"


def test_ingest_config_and_usage_db_follow_exported_dirs(monkeypatch, tmp_path):
    mod = _load("ingest_cli_env", "skills/document-ingest/ingest.py")
    ctx = tmp_path / "agents" / "fin"
    shared = tmp_path
    monkeypatch.setenv("CURUNIR_CONTEXT_DIR", str(ctx))
    monkeypatch.setenv("CURUNIR_SHARED_DIR", str(shared))
    seen = {}

    async def fake_ingest(path, config, usage_store=None):
        seen["config"] = config
        seen["usage_db"] = usage_store.db_path
        return "# card"

    monkeypatch.setattr(mod, "ingest_document", fake_ingest)
    assert mod.main(["doc.txt"]) == 0
    assert seen["config"].context_dir == ctx
    assert seen["config"].shared_dir == shared
    assert seen["config"].identity_file == ctx / "identity.md"
    assert seen["usage_db"] == shared / "usage.db"


def test_ingest_without_env_keeps_legacy_layout(monkeypatch, tmp_path):
    mod = _load("ingest_cli_env_legacy", "skills/document-ingest/ingest.py")
    monkeypatch.delenv("CURUNIR_CONTEXT_DIR", raising=False)
    monkeypatch.delenv("CURUNIR_SHARED_DIR", raising=False)
    seen = {}

    async def fake_ingest(path, config, usage_store=None):
        seen["config"] = config
        return "# card"

    monkeypatch.setattr(mod, "ingest_document", fake_ingest)
    assert mod.main(["doc.txt", "--usage-db", str(tmp_path / "u.db")]) == 0
    from src.config import AgentConfig

    assert seen["config"].context_dir == AgentConfig().context_dir
    assert seen["config"].identity_file == AgentConfig().identity_file
