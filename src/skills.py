# src/skills.py
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


DEFAULT_MAX_OUTPUT_TOKENS = 2000


@dataclass(frozen=True)
class SkillDef:
    name: str
    description: str
    tools: list[str]
    max_iterations: int
    max_output_tokens: int
    body: str


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


def _strip_frontmatter(text: str) -> str:
    """Return everything after the closing `---` of the frontmatter block."""
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    return parts[2].lstrip("\n")


def load_skill_def(name: str, skills_dir: Path) -> SkillDef | None:
    """Load a skill as a sub-agent definition. Returns None if missing or invalid.

    Required frontmatter fields: name, description, tools, max_iterations.
    Optional: max_output_tokens (defaults to DEFAULT_MAX_OUTPUT_TOKENS).
    """
    path = skills_dir / name / "SKILL.md"
    if not path.exists():
        return None

    text = path.read_text()
    fm = parse_frontmatter(text)
    required = ("name", "description", "tools", "max_iterations")
    if not all(k in fm for k in required):
        logger.warning("Skill %s missing required frontmatter fields", name)
        return None

    body = _strip_frontmatter(text)
    return SkillDef(
        name=fm["name"],
        description=fm["description"],
        tools=fm["tools"] if isinstance(fm["tools"], list) else [fm["tools"]],
        max_iterations=int(fm["max_iterations"]),
        max_output_tokens=int(fm.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)),
        body=body,
    )
