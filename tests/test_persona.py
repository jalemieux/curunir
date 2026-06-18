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
        "keys:\n  - FRED_API_KEY\n",
    )
    p = load_persona("finance")
    assert p == Persona(
        name="finance",
        description="money helper",
        skills=["identity", "financial-analysis"],
        keys=["FRED_API_KEY"],
    )


def test_skills_omitted_means_none(make_bundle):
    # Omitting `skills:` is how the default persona allows every skill on disk.
    make_bundle("open", "name: open\n")
    p = load_persona("open")
    assert p.skills is None
    assert p.keys == []


def test_skills_empty_list_raises(make_bundle):
    make_bundle("empty", "name: empty\nskills: []\n")
    with pytest.raises(ValueError, match="non-empty list"):
        load_persona("empty")


def test_missing_bundle_raises_filenotfound(make_bundle):
    with pytest.raises(FileNotFoundError, match="Persona 'ghost' not found"):
        load_persona("ghost")


def test_malformed_yaml_raises_valueerror(make_bundle):
    make_bundle("bad", "skills: [unclosed\n")
    with pytest.raises(ValueError, match="Malformed persona manifest"):
        load_persona("bad")


def test_warn_missing_keys_returns_absent_names(make_bundle, caplog):
    p = Persona("f", "", ["identity"], ["FRED_API_KEY", "OTHER"])
    with caplog.at_level(logging.WARNING):
        missing = warn_missing_keys(p, {"OTHER": "set"})
    assert missing == ["FRED_API_KEY"]
    assert "FRED_API_KEY" in caplog.text


def test_default_bundle_parses_from_repo():
    # Default persona always ships; omits `skills:` to allow every skill.
    p = load_persona("default")
    assert p.name == "default"
    assert p.skills is None


def test_finance_bundle_parses_from_repo():
    p = load_persona("finance")
    assert p.name == "finance"
    assert "financial-analysis" in p.skills
    assert "investment-memo" in p.skills
    assert p.skills  # non-empty absolute allowlist


def test_finance_bundle_allows_tax_strategy():
    # The finance persona advertises "tax strategy" — the backing skill must be
    # in its absolute allowlist (and on disk, per the check below).
    p = load_persona("finance")
    assert "tax-strategy" in p.skills


def test_finance_bundle_skills_exist_on_disk():
    # Every allowlisted skill must resolve to a skills/<name>/SKILL.md so a
    # typo in persona.yaml is caught instead of silently dropping a skill.
    p = load_persona("finance")
    for name in p.skills:
        assert (Path("skills") / name / "SKILL.md").exists(), name


def _skills_depending_on_email_send() -> set[str]:
    """Skills whose delivery step loads the `email-send` skill.

    Auto-discovered by scanning each skill's SKILL.md for an `email-send`
    reference so a new delivery-capable skill is covered without editing this
    test. `email-send` itself is excluded.
    """
    depends = set()
    for skill_md in Path("skills").glob("*/SKILL.md"):
        name = skill_md.parent.name
        if name == "email-send":
            continue
        if "email-send" in skill_md.read_text():
            depends.add(name)
    return depends


def test_email_send_dependency_is_discoverable():
    # Guards the test below: if no skill references email-send the invariant is
    # vacuous and silently stops protecting anything. `digest` is the anchor.
    assert "digest" in _skills_depending_on_email_send()


@pytest.mark.parametrize(
    "persona_name",
    [p.parent.name for p in Path("personas").glob("*/persona.yaml")],
)
def test_persona_allowlisting_a_delivery_skill_also_allows_email_send(persona_name):
    # A curated allowlist that includes a skill whose delivery step loads
    # `email-send` MUST also allowlist `email-send` — otherwise load_skill
    # rejects it and the scheduled digest is generated but never sent (the
    # finance scheduled-email outage). Personas with no allowlist (skills=None)
    # already reach every skill, so they're exempt.
    p = load_persona(persona_name)
    if p.skills is None:
        return
    needs_email_send = _skills_depending_on_email_send() & set(p.skills)
    if needs_email_send:
        assert "email-send" in p.skills, (
            f"persona '{persona_name}' allowlists {sorted(needs_email_send)} "
            f"(which load email-send) but not email-send itself"
        )


def _finance_prompts_text() -> str:
    """Concatenated text of every finance persona prompt layer, as shipped.

    These conventions live in the persona bundle's prompts/*.md (the framework
    behavior layer), so assert against the real files rather than a fixture.
    """
    prompts_dir = Path("personas") / "finance" / "prompts"
    return "\n".join(
        p.read_text() for p in sorted(prompts_dir.glob("*.md"))
    )


def test_finance_prompts_define_unsourced_convention():
    # #402: any external factual claim not grounded in a tool/skill/web_fetch
    # result must be tagged [UNSOURCED], reinforcing the no-general-knowledge
    # guardrail. The literal tag is what the agent emits, so guard the literal.
    text = _finance_prompts_text()
    assert "[UNSOURCED]" in text


def test_finance_prompts_define_show_your_work_convention():
    # #402: projections and derived figures must surface their inputs and the
    # arithmetic, not assert a bare number.
    text = _finance_prompts_text().lower()
    assert "show your work" in text


def test_finance_prompts_standardize_emitted_tables():
    # #402: the finance skills emit standardized markdown tables (holdings /
    # allocation / P&L) with all summation left to the engine ("reads never
    # re-sum"). Guard that the table convention block ships.
    text = _finance_prompts_text().lower()
    assert "standard table" in text
    for kind in ("holdings", "allocation", "p&l"):
        assert kind in text, kind


def test_companion_bundle_parses_from_repo():
    p = load_persona("companion")
    assert p.name == "companion"
    assert p.skills  # non-empty absolute allowlist
    # The companion grounds technique suggestions in research.
    assert "deep-research" in p.skills
    assert "web-search" in p.skills


def test_companion_bundle_declares_search_key():
    # web-search backend needs BRAVE_API_KEY; the bundle documents it.
    p = load_persona("companion")
    assert "BRAVE_API_KEY" in p.keys


def test_companion_bundle_skills_exist_on_disk():
    # Every allowlisted skill must resolve to a skills/<name>/SKILL.md so a
    # typo in persona.yaml is caught instead of silently dropping a skill.
    p = load_persona("companion")
    for name in p.skills:
        assert (Path("skills") / name / "SKILL.md").exists(), name
