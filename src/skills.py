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
    portal_summary: str | None = None


def load_registry(skill_dirs: list[Path]) -> dict[str, Skill]:
    """Scan skill dirs in order and return enabled skills keyed by name.

    On name collision, first-seen wins — so passing [system_dir, user_dir]
    makes system skills shadow user skills. Missing dirs are silently skipped.
    Skills with `disabled: true` or missing `name`/`description` are excluded.
    """
    registry: dict[str, Skill] = {}
    for skills_dir in skill_dirs:
        if not skills_dir.exists():
            continue

        for skill_file in sorted(skills_dir.rglob("SKILL.md")):
            fm = parse_frontmatter(skill_file.read_text())
            if "name" not in fm or "description" not in fm:
                continue
            if fm.get("disabled", "").lower() in _TRUTHY:
                logger.info("skipping disabled skill: %s", fm["name"])
                continue
            name = fm["name"]
            if name in registry:
                logger.warning(
                    "skill '%s' at %s shadowed by earlier entry at %s",
                    name,
                    skill_file,
                    registry[name].path,
                )
                continue
            registry[name] = Skill(
                name=name,
                description=fm["description"],
                path=skill_file,
                portal_summary=fm.get("portal_summary") or None,
            )
    return registry


def build_skill_manifest(skill_dirs: list[Path]) -> str:
    """Return markdown table of enabled skills (name + description)."""
    registry = load_registry(skill_dirs)
    if not registry:
        logger.info("no skills found in %s", [str(d) for d in skill_dirs])
        return ""

    logger.info("discovered %d skills: %s", len(registry), ", ".join(registry))

    lines = [
        "## Available Skills",
        "| Skill | When to Use |",
        "|-------|-------------|",
    ]
    for skill in sorted(registry.values(), key=lambda s: s.name):
        lines.append(f"| {skill.name} | {skill.description} |")
    return "\n".join(lines)


def _display_name(name: str) -> str:
    """Derive a user-facing label: 'investment-memo' -> 'Investment memo'."""
    words = name.replace("-", " ").replace("_", " ")
    return words[:1].upper() + words[1:]


def portal_skill_list(skill_dirs: list[Path]) -> list[dict]:
    """User-facing skills for the portal picker.

    Returns only skills that opted in with a non-empty `portal_summary`,
    sorted by name. Each entry: {name, display_name, summary}.
    """
    registry = load_registry(skill_dirs)
    out = []
    for skill in registry.values():
        if not skill.portal_summary:
            continue
        out.append({
            "name": skill.name,
            "display_name": _display_name(skill.name),
            "summary": skill.portal_summary,
        })
    out.sort(key=lambda s: s["name"])
    return out


def load_skill(name: str, skill_dirs: list[Path]) -> str:
    """Load full SKILL.md content by name, honoring registry shadowing rules."""
    registry = load_registry(skill_dirs)
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
