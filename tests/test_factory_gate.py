"""Tests for .github/scripts/factory_gate.py, the risk-tier check.

The classification cases mirror jalemieux/code_factory tests/test_spine.py
so the vendored rules cannot drift from the factory's without a test
failing on one side.
"""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "factory_gate.py"
_spec = importlib.util.spec_from_file_location("factory_gate", _SCRIPT)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def globs():
    """The real .codefactory.yml, so these tests also validate the file."""
    return gate.load_tiers((REPO_ROOT / gate.CONFIG_PATH).read_text())


@pytest.mark.parametrize("path,tier", [
    ("src/llm.py", "A"),
    ("src/agent/planner/steps/refine.py", "A"),
    ("src/channels/email/inbound.py", "A"),
    ("src/tools/schemas.py", "A"),
    ("src/tools/delegate.py", "A"),
    ("run.py", "A"),
    ("portal/ws_agent.py", "A"),
    ("portal/wsgi.py", "C"),          # `ws_` prefix is literal
    ("src/agent_utils.py", "C"),      # sibling of src/agent/, outside it
    ("src/tools/weather.py", "C"),
    ("skills/foo/SKILL.md", "B"),
    ("personas/bard.md", "B"),
    ("README.md", "C"),
])
def test_tier_of_file(globs, path, tier):
    assert gate.classify([path], globs) == tier


def test_mixed_diff_takes_strictest(globs):
    assert gate.classify(["README.md", "skills/foo/SKILL.md"], globs) == "B"
    assert gate.classify(["skills/foo/SKILL.md", "src/tools/dispatcher.py", "README.md"], globs) == "A"


def test_empty_diff_is_plan_not_a_tier(globs):
    assert gate.classify([], globs) == gate.PLAN
    assert gate.PLAN not in gate.TIERS


def test_security_override(globs):
    assert gate.classify(["README.md"], globs, title="[Security] harden rekey") == "A"
    assert gate.classify(["README.md"], globs, labels=["security-fix"]) == "A"
    assert gate.classify([], globs, title="[security] plan") == "A"
    assert gate.classify(["README.md"], globs, title="fix docs", labels=["bug"]) == "C"


def test_load_tiers_shapes():
    assert gate.load_tiers("tiers:\n  a: src/**\n  b: []\n") == {"A": ("src/**",), "B": ()}
    assert gate.load_tiers("# empty\n") == {"A": (), "B": ()}
    for bad in ("tiers: {C: [x]}", "- a list", "tiers: 42", "tiers: {A: [1]}", "tiers: {A: ['']}", "tiers: [unclosed"):
        with pytest.raises(gate.ConfigError):
            gate.load_tiers(bad)


def test_summary_lists_each_file_with_its_tier(globs):
    files = ["README.md", "run.py"]
    out = gate.render_summary("A", files, globs, None, None)
    assert "tier **A**" in out
    assert "| `README.md` | C |" in out
    assert "| `run.py` | A |" in out


def test_main_fails_on_empty_diff(monkeypatch, tmp_path, capsys):
    (tmp_path / gate.CONFIG_PATH).write_text("tiers: {A: [src/**]}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPO", "o/r")
    monkeypatch.setenv("PR_NUMBER", "7")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    monkeypatch.setattr(gate, "changed_files", lambda repo, pr: [])
    assert gate.main() == 1
    assert "tier=PLAN" in (tmp_path / "out").read_text()
    assert "plan draft" in (tmp_path / "summary").read_text()


def test_main_passes_and_reports_tier(monkeypatch, tmp_path):
    (tmp_path / gate.CONFIG_PATH).write_text("tiers: {A: [src/**]}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPO", "o/r")
    monkeypatch.setenv("PR_NUMBER", "7")
    monkeypatch.setenv("PR_TITLE", "docs")
    monkeypatch.setenv("PR_LABELS", '["bug"]')
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setattr(gate, "changed_files", lambda repo, pr: ["README.md"])
    assert gate.main() == 0
    assert "tier=C" in (tmp_path / "out").read_text()


def test_main_fails_without_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPO", "o/r")
    monkeypatch.setenv("PR_NUMBER", "7")
    assert gate.main() == 1
