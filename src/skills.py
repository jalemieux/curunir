# src/skills.py
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_TRUTHY = {"true", "1", "yes", "on"}


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path


def load_registry(skills_dir: Path) -> dict[str, Skill]:
    """Scan skills dir and return enabled skills keyed by name.

    Skills with `disabled: true` in their frontmatter are excluded.
    Skills lacking `name` or `description` are ignored.
    """
    registry: dict[str, Skill] = {}
    if not skills_dir.exists():
        return registry

    for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
        fm = parse_frontmatter(skill_file.read_text())
        if "name" not in fm or "description" not in fm:
            continue
        if fm.get("disabled", "").lower() in _TRUTHY:
            logger.info("skipping disabled skill: %s", fm["name"])
            continue
        registry[fm["name"]] = Skill(
            name=fm["name"],
            description=fm["description"],
            path=skill_file,
        )
    return registry


def build_skill_manifest(skills_dir: Path) -> str:
    """Return markdown table of enabled skills (name + description)."""
    registry = load_registry(skills_dir)
    if not registry:
        logger.info("no skills found in %s", skills_dir)
        return ""

    logger.info("discovered %d skills: %s", len(registry), ", ".join(registry))

    lines = [
        "## Available Skills",
        "| Skill | When to Use |",
        "|-------|-------------|",
    ]
    for skill in registry.values():
        lines.append(f"| {skill.name} | {skill.description} |")
    return "\n".join(lines)


def load_skill(name: str, skills_dir: Path) -> str:
    """Load full SKILL.md content by name, honoring registry exclusions."""
    registry = load_registry(skills_dir)
    skill = registry.get(name)
    if skill is None:
        return f"Skill not found: {name}"
    return skill.path.read_text()


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
