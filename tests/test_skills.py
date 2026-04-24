# tests/test_skills.py
from pathlib import Path

from src.skills import build_skill_manifest, load_registry, load_skill, parse_frontmatter


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


class TestBuildSkillManifest:
    def test_no_skills(self, tmp_path):
        assert build_skill_manifest(tmp_path) == ""

    def test_one_skill(self, tmp_path):
        skill_dir = tmp_path / "research"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: research\ndescription: When user asks to investigate\n---\n\n# Research"
        )
        result = build_skill_manifest(tmp_path)
        assert "| research | When user asks to investigate |" in result
        assert "## Available Skills" in result

    def test_multiple_skills_sorted(self, tmp_path):
        for name in ["zebra", "alpha"]:
            d = tmp_path / name
            d.mkdir()
            (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} desc\n---\n")
        result = build_skill_manifest(tmp_path)
        lines = result.splitlines()
        skill_lines = [l for l in lines if l.startswith("| ") and "Skill" not in l]
        assert "alpha" in skill_lines[0]
        assert "zebra" in skill_lines[1]

    def test_nonexistent_dir(self, tmp_path):
        result = build_skill_manifest(tmp_path / "nonexistent")
        assert result == ""


class TestLoadSkill:
    def test_existing_skill(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        body = "---\nname: my-skill\ndescription: test\n---\n\n# My Skill\nInstructions here."
        (skill_dir / "SKILL.md").write_text(body)
        result = load_skill("my-skill", tmp_path)
        assert result == body

    def test_missing_skill(self, tmp_path):
        result = load_skill("nonexistent", tmp_path)
        assert "not found" in result.lower()

    def test_disabled_skill_not_loadable(self, tmp_path):
        skill_dir = tmp_path / "retired"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: retired\ndescription: old\ndisabled: true\n---\n# body"
        )
        result = load_skill("retired", tmp_path)
        assert "not found" in result.lower()


class TestDisabledFlag:
    def test_disabled_excluded_from_manifest(self, tmp_path):
        for name, disabled in [("alpha", False), ("beta", True)]:
            d = tmp_path / name
            d.mkdir()
            extra = "disabled: true\n" if disabled else ""
            (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} desc\n{extra}---\n")
        manifest = build_skill_manifest(tmp_path)
        assert "alpha" in manifest
        assert "beta" not in manifest

    def test_disabled_excluded_from_registry(self, tmp_path):
        d = tmp_path / "gone"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: gone\ndescription: x\ndisabled: yes\n---\n")
        assert load_registry(tmp_path) == {}
