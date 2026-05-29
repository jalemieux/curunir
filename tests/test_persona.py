# tests/test_persona.py
import logging
from pathlib import Path

import pytest

from src.persona import Persona, load_persona, warn_missing_keys


@pytest.fixture
def make_bundle(tmp_path, monkeypatch):
    """Create personas/<name>/persona.yaml under a temp PERSONAS_DIR."""
    personas_dir = tmp_path / "personas"
    monkeypatch.setattr("src.persona.PERSONAS_DIR", personas_dir)

    def _make(name: str, yaml_text: str) -> Path:
        bundle = personas_dir / name
        bundle.mkdir(parents=True)
        (bundle / "persona.yaml").write_text(yaml_text)
        return bundle

    return _make


def test_loads_full_manifest(make_bundle):
    make_bundle(
        "finance",
        "name: finance\n"
        "description: money helper\n"
        "skills:\n  - identity\n  - financial-analysis\n"
        "tools:\n  - read\n  - edit\n"
        "keys:\n  - FRED_API_KEY\n",
    )
    p = load_persona("finance")
    assert p == Persona(
        name="finance",
        description="money helper",
        skills=["identity", "financial-analysis"],
        tools=["read", "edit"],
        keys=["FRED_API_KEY"],
    )


def test_tools_omitted_means_none(make_bundle):
    make_bundle("min", "name: min\nskills:\n  - identity\n")
    p = load_persona("min")
    assert p.tools is None
    assert p.keys == []


def test_missing_bundle_raises_filenotfound(make_bundle):
    with pytest.raises(FileNotFoundError, match="Persona 'ghost' not found"):
        load_persona("ghost")


def test_malformed_yaml_raises_valueerror(make_bundle):
    make_bundle("bad", "skills: [unclosed\n")
    with pytest.raises(ValueError, match="Malformed persona manifest"):
        load_persona("bad")


def test_missing_skills_raises_valueerror(make_bundle):
    make_bundle("noskills", "name: noskills\n")
    with pytest.raises(ValueError, match="at least one skill"):
        load_persona("noskills")


def test_warn_missing_keys_returns_absent_names(make_bundle, caplog):
    p = Persona("f", "", ["identity"], None, ["FRED_API_KEY", "OTHER"])
    with caplog.at_level(logging.WARNING):
        missing = warn_missing_keys(p, {"OTHER": "set"})
    assert missing == ["FRED_API_KEY"]
    assert "FRED_API_KEY" in caplog.text


def test_finance_bundle_parses_from_repo():
    # Loads the real personas/finance/ shipped in the repo (PERSONAS_DIR
    # default is Path("personas"); no monkeypatch here).
    p = load_persona("finance")
    assert p.name == "finance"
    assert "financial-analysis" in p.skills
    assert "investment-memo" in p.skills
    assert p.skills  # non-empty absolute allowlist


def test_finance_bundle_skills_exist_on_disk():
    # Every allowlisted skill must resolve to a skills/<name>/SKILL.md so a
    # typo in persona.yaml is caught instead of silently dropping a skill.
    p = load_persona("finance")
    for name in p.skills:
        assert (Path("skills") / name / "SKILL.md").exists(), name
