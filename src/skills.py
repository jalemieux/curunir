# src/skills.py
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


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
        logger.info("no skills found in %s", skills_dir)
        return ""

    logger.info("discovered %d skills: %s", len(skills), ", ".join(n for n, _ in skills))

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
    """Extract YAML frontmatter from markdown. Returns {} if no frontmatter."""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        loaded = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
