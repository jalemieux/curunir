# tests/test_gtm_router.py
"""Guards the GTM front-door router skill (#448).

The seven `gtm-*` stage skills trigger on pipeline-stage vocabulary, so
natural GTM requests ("review my landing page", "help me with go-to-market")
route to none of them. The `gtm` router is a broad-triggering front door that
classifies intent and loads the right stage skill(s) via `load_skill`, without
modifying the stage skills.
"""
import re
from pathlib import Path

import pytest

from src.persona import load_persona
from src.skills import load_registry, load_skill, parse_frontmatter

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_DIR = _REPO_ROOT / "skills"
_ROUTER = "gtm"


def _stage_skills() -> set[str]:
    """The discrete gtm-* pipeline skills (the router itself is `gtm`, no dash)."""
    return {
        p.parent.name
        for p in _SKILLS_DIR.glob("gtm-*/SKILL.md")
    }


def _router_text() -> str:
    return (_SKILLS_DIR / _ROUTER / "SKILL.md").read_text()


def test_router_skill_exists_and_loadable():
    body = load_skill(_ROUTER, [_SKILLS_DIR])
    assert "not found" not in body.lower()
    fm = parse_frontmatter(body)
    assert fm["name"] == _ROUTER


def test_router_registers_and_is_visible():
    # Not hidden — the whole point is that the agent routes to it on its own.
    reg = load_registry([_SKILLS_DIR])
    assert _ROUTER in reg
    assert reg[_ROUTER].hidden is False


def test_router_description_catches_natural_gtm_phrasing():
    desc = parse_frontmatter(_router_text())["description"].lower()
    # The natural phrasing from #448 that the stage skills omit by design.
    for phrase in ("go-to-market", "landing page", "positioning", "launch"):
        assert phrase in desc, f"router description should catch {phrase!r}"


def test_router_body_references_every_stage_skill():
    # Routing coverage: every discrete stage skill must be reachable from the
    # router, so no GTM intent dead-ends. Fails if a new gtm-* skill is added
    # without wiring it into the router.
    body = _router_text()
    missing = sorted(s for s in _stage_skills() if s not in body)
    assert not missing, f"router does not reference stage skills: {missing}"


def test_router_instructs_load_skill_handoff():
    body = _router_text()
    assert "load_skill" in body


def test_router_is_orchestrating_not_single_routing():
    # Reviewer chose orchestration: the router may load and combine MORE THAN
    # ONE stage skill for a compound request, and must NOT carry the
    # single-routing "do not substitute another skill" lockout.
    body = _router_text().lower()
    assert "do not substitute" not in body
    assert "combine" in body or "synthesize" in body or "multiple" in body


def test_router_covers_landing_page_critique_gap():
    # The gap case from #448: there is no dedicated frontend-critique skill, so
    # the router must explicitly tell the model to compose from existing stage
    # skills rather than declaring the case unsupported.
    body = _router_text().lower()
    assert "landing page" in body


def test_marketing_persona_allowlists_router():
    p = load_persona("marketing")
    assert _ROUTER in p.skills


def test_router_does_not_modify_stage_skills():
    # Pure additive layer: the stage skills keep their narrow pipeline triggers.
    # (Anchors the reviewer's "do not modify the existing pipeline skills" call.)
    for stage in _stage_skills():
        desc = parse_frontmatter(
            (_SKILLS_DIR / stage / "SKILL.md").read_text()
        )["description"]
        assert desc, f"{stage} lost its description"
