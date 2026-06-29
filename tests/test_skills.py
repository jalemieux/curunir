# tests/test_skills.py
import logging
import re
from pathlib import Path

import pytest

from src.skills import (
    build_skill_manifest,
    load_registry,
    load_skill,
    parse_frontmatter,
    portal_skill_list,
)


class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        text = "---\nname: my-skill\ndescription: Do something\n---\n\n# Body"
        result = parse_frontmatter(text)
        assert result == {"name": "my-skill", "description": "Do something"}

    def test_no_frontmatter(self):
        result = parse_frontmatter("# Just a heading")
        assert result == {}

    def test_strips_quotes(self):
        text = '---\nname: "quoted-skill"\ndescription: \'single quoted\'\n---\n'
        result = parse_frontmatter(text)
        assert result == {"name": "quoted-skill", "description": "single quoted"}

    def test_colon_in_value(self):
        text = "---\nname: my-skill\ndescription: Use when: user asks\n---\n"
        result = parse_frontmatter(text)
        assert result["description"] == "Use when: user asks"


def _write_skill(parent, name, description, disabled=False, hidden=False,
                 portal_starter=False):
    d = parent / name
    d.mkdir()
    extra = ""
    if disabled:
        extra += "disabled: true\n"
    if hidden:
        extra += "hidden: true\n"
    if portal_starter:
        extra += "portal_starter: true\n"
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\n# {name}\n"
    )
    return d / "SKILL.md"


class TestBuildSkillManifest:
    def test_no_skills(self, tmp_path):
        assert build_skill_manifest([tmp_path]) == ""

    def test_one_skill(self, tmp_path):
        _write_skill(tmp_path, "research", "When user asks to investigate")
        result = build_skill_manifest([tmp_path])
        assert "| research | When user asks to investigate |" in result
        assert "## Available Skills" in result

    def test_manifest_carries_load_by_name_signpost(self, tmp_path):
        """A one-line preamble tells the agent to load_skill by name rather
        than filesystem-hunting for SKILL.md files (#451)."""
        _write_skill(tmp_path, "research", "When user asks to investigate")
        result = build_skill_manifest([tmp_path])
        # Mentions the load mechanism...
        assert "load_skill" in result
        # ...and discourages filesystem hunting.
        assert "find" in result.lower()
        # The signpost sits between the header and the table.
        header_idx = result.index("## Available Skills")
        table_idx = result.index("| Skill | When to Use |")
        signpost_idx = result.index("load_skill")
        assert header_idx < signpost_idx < table_idx

    def test_signpost_present_with_single_skill(self, tmp_path):
        _write_skill(tmp_path, "solo", "the only skill")
        result = build_skill_manifest([tmp_path])
        assert "load_skill" in result

    def test_multiple_skills_sorted(self, tmp_path):
        _write_skill(tmp_path, "zebra", "zebra desc")
        _write_skill(tmp_path, "alpha", "alpha desc")
        result = build_skill_manifest([tmp_path])
        skill_lines = [l for l in result.splitlines() if l.startswith("| ") and "Skill" not in l]
        assert "alpha" in skill_lines[0]
        assert "zebra" in skill_lines[1]

    def test_nonexistent_dir(self, tmp_path):
        assert build_skill_manifest([tmp_path / "nonexistent"]) == ""


class TestLoadSkill:
    def test_existing_skill(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        body = "---\nname: my-skill\ndescription: test\n---\n\n# My Skill\nInstructions."
        (skill_dir / "SKILL.md").write_text(body)
        assert load_skill("my-skill", [tmp_path]) == body

    def test_missing_skill(self, tmp_path):
        assert "not found" in load_skill("nonexistent", [tmp_path]).lower()

    def test_disabled_skill_not_loadable(self, tmp_path):
        _write_skill(tmp_path, "retired", "old", disabled=True)
        assert "not found" in load_skill("retired", [tmp_path]).lower()


class TestDisabledFlag:
    def test_disabled_excluded_from_manifest(self, tmp_path):
        _write_skill(tmp_path, "alpha", "alpha desc")
        _write_skill(tmp_path, "beta", "beta desc", disabled=True)
        manifest = build_skill_manifest([tmp_path])
        assert "alpha" in manifest
        assert "beta" not in manifest

    def test_disabled_excluded_from_registry(self, tmp_path):
        _write_skill(tmp_path, "gone", "x", disabled=True)
        assert load_registry([tmp_path]) == {}


class TestHiddenFlag:
    def test_hidden_excluded_from_manifest(self, tmp_path):
        _write_skill(tmp_path, "alpha", "alpha desc")
        _write_skill(tmp_path, "newthing", "hidden desc", hidden=True)
        manifest = build_skill_manifest([tmp_path])
        assert "alpha" in manifest
        assert "newthing" not in manifest

    def test_hidden_still_in_registry(self, tmp_path):
        _write_skill(tmp_path, "newthing", "hidden desc", hidden=True)
        registry = load_registry([tmp_path])
        assert "newthing" in registry
        assert registry["newthing"].hidden is True

    def test_hidden_still_loadable(self, tmp_path):
        path = _write_skill(tmp_path, "newthing", "hidden desc", hidden=True)
        assert load_skill("newthing", [tmp_path]) == path.read_text()

    def test_hidden_defaults_false(self, tmp_path):
        _write_skill(tmp_path, "plain", "agent desc")
        assert load_registry([tmp_path])["plain"].hidden is False

    def test_only_hidden_skill_yields_empty_manifest(self, tmp_path):
        _write_skill(tmp_path, "newthing", "hidden desc", hidden=True)
        assert build_skill_manifest([tmp_path]) == ""


class TestMultipleDirs:
    def test_merges_from_both_dirs(self, tmp_path):
        sys_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        sys_dir.mkdir()
        user_dir.mkdir()
        _write_skill(sys_dir, "web-search", "system: web search")
        _write_skill(user_dir, "journal", "user: journaling")

        manifest = build_skill_manifest([sys_dir, user_dir])
        assert "web-search" in manifest
        assert "journal" in manifest

    def test_missing_user_dir_is_silent(self, tmp_path, caplog):
        sys_dir = tmp_path / "system"
        sys_dir.mkdir()
        _write_skill(sys_dir, "web-search", "system: web search")

        with caplog.at_level(logging.WARNING):
            manifest = build_skill_manifest([sys_dir, tmp_path / "nonexistent"])
        assert "web-search" in manifest
        # No warning should fire for missing dir.
        assert not any("nonexistent" in r.message for r in caplog.records)

    def test_system_wins_on_collision(self, tmp_path, caplog):
        sys_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        sys_dir.mkdir()
        user_dir.mkdir()
        _write_skill(sys_dir, "clash", "system version")
        _write_skill(user_dir, "clash", "user version")

        with caplog.at_level(logging.WARNING):
            manifest = build_skill_manifest([sys_dir, user_dir])
        assert "system version" in manifest
        assert "user version" not in manifest
        assert any(
            "clash" in r.message and "shadowed" in r.message for r in caplog.records
        )

    def test_load_skill_respects_order(self, tmp_path):
        sys_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        sys_dir.mkdir()
        user_dir.mkdir()
        sys_path = _write_skill(sys_dir, "clash", "system version")
        _write_skill(user_dir, "clash", "user version")

        assert load_skill("clash", [sys_dir, user_dir]) == sys_path.read_text()

    def test_load_user_skill_when_no_collision(self, tmp_path):
        sys_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        sys_dir.mkdir()
        user_dir.mkdir()
        user_path = _write_skill(user_dir, "journal", "user: journaling")

        assert load_skill("journal", [sys_dir, user_dir]) == user_path.read_text()

    def test_load_unknown_returns_not_found(self, tmp_path):
        sys_dir = tmp_path / "system"
        sys_dir.mkdir()
        result = load_skill("nope", [sys_dir, tmp_path / "missing"])
        assert "not found" in result.lower()


def _write_nested_skill(parent, *path_parts, description="nested"):
    """Create skills/<parent>/.../SKILL.md at arbitrary nesting depth."""
    d = parent
    for part in path_parts:
        d = d / part
        d.mkdir(exist_ok=True)
    name = path_parts[-1]
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n"
    )
    return d / "SKILL.md"


class TestNestedDiscovery:
    def test_nested_skill_is_discovered(self, tmp_path):
        _write_nested_skill(tmp_path, "onboarding", "profile", description="profile sub-skill")
        registry = load_registry([tmp_path])
        assert "profile" in registry
        assert registry["profile"].description == "profile sub-skill"

    def test_top_level_and_nested_coexist(self, tmp_path):
        _write_skill(tmp_path, "top", "a top-level skill")
        _write_nested_skill(tmp_path, "onboarding", "preferences", description="prefs")
        registry = load_registry([tmp_path])
        assert "top" in registry
        assert "preferences" in registry

    def test_parent_and_child_both_register(self, tmp_path):
        """A parent SKILL.md and a sibling subdir SKILL.md both show up."""
        _write_skill(tmp_path, "onboarding", "orchestrator")
        _write_nested_skill(tmp_path, "onboarding", "profile", description="prof")
        registry = load_registry([tmp_path])
        assert "onboarding" in registry
        assert "profile" in registry


def _write_portal_skill(parent, name, description, summary=None,
                        portal_starter=False):
    """Create skills/<name>/SKILL.md with optional portal_* frontmatter."""
    d = parent / name
    d.mkdir()
    lines = [f"name: {name}", f"description: {description}"]
    if summary is not None:
        lines.append(f'portal_summary: "{summary}"')
    if portal_starter:
        lines.append("portal_starter: true")
    (d / "SKILL.md").write_text(
        "---\n" + "\n".join(lines) + f"\n---\n# {name}\n"
    )
    return d / "SKILL.md"


class TestPortalMetadata:
    def test_portal_fields_parsed_into_skill(self, tmp_path):
        _write_portal_skill(tmp_path, "memo", "agent desc",
                            summary="User-facing line")
        skill = load_registry([tmp_path])["memo"]
        assert skill.portal_summary == "User-facing line"

    def test_portal_fields_default_none(self, tmp_path):
        _write_skill(tmp_path, "plain", "agent desc")
        skill = load_registry([tmp_path])["plain"]
        assert skill.portal_summary is None

    def test_portal_starter_parsed_into_skill(self, tmp_path):
        _write_portal_skill(tmp_path, "memo", "agent desc",
                            summary="s", portal_starter=True)
        skill = load_registry([tmp_path])["memo"]
        assert skill.portal_starter is True

    def test_portal_starter_defaults_false(self, tmp_path):
        _write_skill(tmp_path, "plain", "agent desc")
        skill = load_registry([tmp_path])["plain"]
        assert skill.portal_starter is False


class TestPortalSkillList:
    def test_skill_without_portal_summary_excluded(self, tmp_path):
        """Browse panel is gated on portal_summary — plain skills don't appear."""
        _write_portal_skill(tmp_path, "with-summary", "d", summary="visible")
        _write_skill(tmp_path, "without-summary", "d")
        names = [s["name"] for s in portal_skill_list([tmp_path])]
        assert names == ["with-summary"]

    def test_disabled_skill_excluded(self, tmp_path):
        d = tmp_path / "off"
        d.mkdir()
        (d / "SKILL.md").write_text(
            '---\nname: off\ndescription: d\n'
            'portal_summary: "x"\ndisabled: true\n---\n'
        )
        assert portal_skill_list([tmp_path]) == []

    def test_hidden_skill_excluded(self, tmp_path):
        d = tmp_path / "secret"
        d.mkdir()
        (d / "SKILL.md").write_text(
            '---\nname: secret\ndescription: d\n'
            'portal_summary: "x"\nhidden: true\n---\n'
        )
        assert portal_skill_list([tmp_path]) == []

    def test_portal_summary_skill_appears_not_starter(self, tmp_path):
        """portal_summary alone → in browse panel, but not an empty-page starter."""
        _write_portal_skill(tmp_path, "memo", "agent description",
                            summary="User-facing")
        entry = portal_skill_list([tmp_path])[0]
        assert entry["summary"] == "User-facing"
        assert entry["starter"] is False

    def test_portal_starter_skill_appears_and_is_starter(self, tmp_path):
        """portal_summary + portal_starter → in browse panel and a starter."""
        _write_portal_skill(tmp_path, "memo", "agent description",
                            summary="User-facing", portal_starter=True)
        entry = portal_skill_list([tmp_path])[0]
        assert entry["summary"] == "User-facing"
        assert entry["starter"] is True

    def test_portal_starter_without_summary_excluded_and_warns(self, tmp_path, caplog):
        """portal_starter without portal_summary is excluded entirely + warns."""
        _write_skill(tmp_path, "orphan", "d", portal_starter=True)
        with caplog.at_level(logging.WARNING):
            result = portal_skill_list([tmp_path])
        assert result == []
        assert any(
            "orphan" in r.message and "portal_starter" in r.message
            for r in caplog.records
        )

    def test_blank_summary_excluded(self, tmp_path):
        _write_portal_skill(tmp_path, "blank", "agent description", summary="")
        assert portal_skill_list([tmp_path]) == []

    def test_display_name_derived_from_name(self, tmp_path):
        _write_portal_skill(tmp_path, "investment-memo", "d", summary="s")
        assert portal_skill_list([tmp_path])[0]["display_name"] == "Investment memo"

    def test_entries_sorted_by_name(self, tmp_path):
        _write_portal_skill(tmp_path, "zeta", "d", summary="s")
        _write_portal_skill(tmp_path, "alpha", "d", summary="s")
        assert [s["name"] for s in portal_skill_list([tmp_path])] == ["alpha", "zeta"]

    def test_entry_shape(self, tmp_path):
        _write_portal_skill(tmp_path, "memo", "d", summary="A memo",
                            portal_starter=True)
        assert portal_skill_list([tmp_path]) == [
            {"name": "memo", "display_name": "Memo",
             "summary": "A memo", "starter": True}
        ]


def _write_allowlist_skill(skills_dir, name):
    d = skills_dir / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: does {name}\n---\nbody\n"
    )


def test_allowlist_filters_registry(tmp_path):
    skills_dir = tmp_path / "skills"
    for n in ("identity", "financial-analysis", "comfyui"):
        _write_allowlist_skill(skills_dir, n)
    reg = load_registry([skills_dir], allowlist={"identity", "financial-analysis"})
    assert set(reg) == {"identity", "financial-analysis"}


def test_no_allowlist_returns_all(tmp_path):
    skills_dir = tmp_path / "skills"
    for n in ("identity", "comfyui"):
        _write_allowlist_skill(skills_dir, n)
    reg = load_registry([skills_dir])
    assert set(reg) == {"identity", "comfyui"}


def test_unknown_allowlisted_skill_warns_not_crashes(tmp_path, caplog):
    import logging
    skills_dir = tmp_path / "skills"
    _write_allowlist_skill(skills_dir, "identity")
    with caplog.at_level(logging.WARNING):
        reg = load_registry([skills_dir], allowlist={"identity", "ghost"})
    assert set(reg) == {"identity"}
    assert "ghost" in caplog.text


def test_manifest_honors_allowlist(tmp_path):
    skills_dir = tmp_path / "skills"
    for n in ("identity", "comfyui"):
        _write_allowlist_skill(skills_dir, n)
    manifest = build_skill_manifest([skills_dir], allowlist={"identity"})
    assert "identity" in manifest
    assert "comfyui" not in manifest


def test_load_skill_honors_allowlist(tmp_path):
    skills_dir = tmp_path / "skills"
    for n in ("identity", "comfyui"):
        _write_allowlist_skill(skills_dir, n)
    # In-allowlist skill loads.
    body = load_skill("identity", [skills_dir], allowlist={"identity"})
    assert "identity" in body
    # Out-of-allowlist skill is not loadable, even though it exists on disk.
    out = load_skill("comfyui", [skills_dir], allowlist={"identity"})
    assert "not found" in out.lower()


def test_load_skill_no_allowlist_loads_anything(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_allowlist_skill(skills_dir, "comfyui")
    out = load_skill("comfyui", [skills_dir])
    assert "comfyui" in out


def test_portal_list_honors_allowlist(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    for n in ("alpha", "beta"):
        _write_portal_skill(skills_dir, n, "d", summary=f"summary-{n}")
    names = [s["name"] for s in portal_skill_list([skills_dir], allowlist={"alpha"})]
    assert names == ["alpha"]


# Anti-drift guard: the memo skills document the `delegate` sub-agent timeout
# in prose (markdown can't interpolate `delegate._TIMEOUT`), so this test fails
# if the two ever diverge — e.g. the stale "300s" the docs once carried while
# the real limit was 1200s (#412).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_MEMO_SKILLS = ("catalyst-memo", "investment-memo")


def _delegate_timeout() -> int:
    src = (_REPO_ROOT / "src" / "tools" / "delegate.py").read_text()
    m = re.search(r"^_TIMEOUT\s*=\s*(\d+)", src, re.MULTILINE)
    assert m, "could not find _TIMEOUT in src/tools/delegate.py"
    return int(m.group(1))


@pytest.mark.parametrize("skill", _MEMO_SKILLS)
def test_memo_skill_documents_current_delegate_timeout(skill):
    timeout = _delegate_timeout()
    body = (_REPO_ROOT / "skills" / skill / "SKILL.md").read_text()
    # The docs must reference the live timeout...
    assert f"{timeout}s" in body, (
        f"skills/{skill}/SKILL.md does not reference the live delegate "
        f"timeout of {timeout}s (from delegate._TIMEOUT)"
    )
    # ...and must not carry the stale 300s figure (when it isn't the real one).
    if timeout != 300:
        assert "300s" not in body, (
            f"skills/{skill}/SKILL.md still references the stale 300s delegate "
            f"timeout; the real limit is {timeout}s"
        )
