# tests/test_skills.py
from pathlib import Path

from src.skills import SkillDef, build_skill_manifest, load_skill, load_skill_def, parse_frontmatter


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
        text = '---\nname: my-skill\ndescription: "Use when: user asks"\n---\n'
        result = parse_frontmatter(text)
        assert result["description"] == "Use when: user asks"

    def test_list_value(self):
        text = "---\nname: x\ntools: [read, edit, write]\n---\n"
        result = parse_frontmatter(text)
        assert result["tools"] == ["read", "edit", "write"]

    def test_int_value(self):
        text = "---\nname: x\nmax_iterations: 15\n---\n"
        result = parse_frontmatter(text)
        assert result["max_iterations"] == 15

    def test_mixed_types(self):
        text = "---\nname: x\ndescription: Do a thing\ntools: [bash]\nmax_iterations: 5\nmax_output_tokens: 2000\n---\n"
        result = parse_frontmatter(text)
        assert result["name"] == "x"
        assert result["description"] == "Do a thing"
        assert result["tools"] == ["bash"]
        assert result["max_iterations"] == 5
        assert result["max_output_tokens"] == 2000


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
        (skill_dir / "SKILL.md").write_text("# My Skill\nInstructions here.")
        result = load_skill("my-skill", tmp_path)
        assert result == "# My Skill\nInstructions here."

    def test_missing_skill(self, tmp_path):
        result = load_skill("nonexistent", tmp_path)
        assert "not found" in result.lower()


class TestLoadSkillDef:
    def test_loads_all_fields(self, tmp_path):
        d = tmp_path / "my-skill"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\n"
            "name: my-skill\n"
            "description: Does a thing\n"
            "tools: [read, edit]\n"
            "max_iterations: 7\n"
            "max_output_tokens: 3000\n"
            "---\n\n"
            "# Body text here"
        )
        defn = load_skill_def("my-skill", tmp_path)
        assert defn.name == "my-skill"
        assert defn.description == "Does a thing"
        assert defn.tools == ["read", "edit"]
        assert defn.max_iterations == 7
        assert defn.max_output_tokens == 3000
        assert "# Body text here" in defn.body

    def test_defaults_when_optional_missing(self, tmp_path):
        d = tmp_path / "my-skill"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\n"
            "name: my-skill\n"
            "description: Does a thing\n"
            "tools: [read]\n"
            "max_iterations: 5\n"
            "---\n"
        )
        defn = load_skill_def("my-skill", tmp_path)
        assert defn.max_output_tokens == 2000  # default

    def test_missing_skill(self, tmp_path):
        assert load_skill_def("nope", tmp_path) is None

    def test_missing_required_fields_returns_none(self, tmp_path):
        d = tmp_path / "bad-skill"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: bad-skill\n---\n# No description, tools, or max_iterations")
        assert load_skill_def("bad-skill", tmp_path) is None
