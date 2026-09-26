"""Path-scoping lint for skill and persona markdown (agents-and-containers, phase 1).

Markdown that enters a prompt names the agent's private dir as ``{{context}}``
and the container's shared area as ``{{shared}}``; ``render_paths``
substitutes them at load time. A raw ``context/memory`` etc. would silently
point every non-default agent at the legacy agent's files, so this lint
keeps the literals from creeping back. It also checks that the legacy
single-agent render is byte-identical to the pre-placeholder text.
"""
import re
from pathlib import Path

import pytest

from src.skills import load_registry, load_skill, parse_frontmatter, render_paths

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "skills"
PERSONAS = REPO / "personas"

# The literals the placeholders replaced. ``{{context}}/memory`` does not
# match: the text there is ``context}}/memory``.
_RAW_PRIVATE = r"context/(memory|identity\.md|conversations|schedules|skills)"
_RAW_SHARED = r"context/(workspace|uploads)"
_RAW_STALE = r"context/behavior\.md"  # no longer read at boot; refs are stale
_RAW = re.compile(rf"(?<![\w./{{])({_RAW_PRIVATE}|{_RAW_SHARED}|{_RAW_STALE})")

_PLACEHOLDER = re.compile(r"\{\{\s*(context|shared)\s*\}\}")


def _prompt_markdown() -> list[Path]:
    """Every markdown file that can enter a prompt."""
    files = sorted(SKILLS.rglob("*.md"))
    files += sorted(PERSONAS.glob("*/prompts/*.md"))
    return files


@pytest.mark.parametrize("path", _prompt_markdown(), ids=lambda p: str(p.relative_to(REPO)))
def test_no_raw_context_paths_in_prompt_markdown(path):
    hits = [
        f"{i}: {line.strip()}"
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if _RAW.search(line)
    ]
    assert not hits, (
        f"{path.relative_to(REPO)} names context/ literally; use {{{{context}}}}/... "
        f"for private paths or {{{{shared}}}}/... for workspace/uploads:\n"
        + "\n".join(hits)
    )


@pytest.mark.parametrize("path", sorted(SKILLS.rglob("SKILL.md")), ids=lambda p: str(p.relative_to(REPO)))
def test_frontmatter_shown_unrendered_carries_no_placeholders(path):
    """description / portal_summary reach the portal and /help unrendered."""
    fm = parse_frontmatter(path.read_text())
    for key in ("description", "portal_summary"):
        assert not _PLACEHOLDER.search(fm.get(key, "")), f"{path}: {key} has a placeholder"


def test_legacy_render_of_every_catalog_skill_is_byte_identical_to_literals():
    """With one legacy agent, rendering == replacing placeholders by ``context``.

    That is exactly the text the skills carried before the placeholders, so
    the single-agent prompt (and its cache prefix) is unchanged.
    """
    registry = load_registry([SKILLS])
    assert registry, "no catalog skills found"
    for name, skill in registry.items():
        raw = skill.path.read_text()
        expected = _PLACEHOLDER.sub(lambda m: "context", raw)
        assert load_skill(name, [SKILLS]) == expected, name
        assert "{{context}}" not in expected and "{{shared}}" not in expected


def test_placeholders_are_used_somewhere():
    """Guard against the lint passing vacuously after a mass revert."""
    text = "".join(p.read_text() for p in _prompt_markdown())
    assert "{{context}}" in text and "{{shared}}" in text


def test_render_paths_shared_and_private_differ_for_a_non_default_agent():
    out = render_paths(
        "{{context}}/memory/x {{shared}}/workspace/y",
        {"context": "context/agents/finance", "shared": "context"},
    )
    assert out == "context/agents/finance/memory/x context/workspace/y"
