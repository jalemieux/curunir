# src/skills.py
from pathlib import Path


def build_skill_manifest(skills_dir: Path) -> str:
    """Scan skills dir, return markdown table of name + description."""
    if not skills_dir.exists():
        return ""

    skills = []
    for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
        frontmatter = parse_frontmatter(skill_file.read_text())
        if "name" in frontmatter and "description" in frontmatter:
            skills.append((frontmatter["name"], frontmatter["description"]))

    if not skills:
        return ""

    lines = [
        "## Available Skills",
        "| Skill | When to Use |",
        "|-------|-------------|",
    ]
    for name, desc in skills:
        lines.append(f"| {name} | {desc} |")
    return "\n".join(lines)


def load_skill(name: str, skills_dir: Path) -> str:
    """Load full SKILL.md content by name."""
    path = skills_dir / name / "SKILL.md"
    if not path.exists():
        return f"Skill not found: {name}"
    return path.read_text()


def parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from markdown."""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    fm = parts[1]
    result = {}
    for line in fm.strip().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip().strip("'\"")
    return result
