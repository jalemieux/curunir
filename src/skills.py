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
    portal_starter: bool = False
    hidden: bool = False


def load_registry(
    skill_dirs: list[Path], allowlist: set[str] | None = None
) -> dict[str, Skill]:
    """Scan skill dirs in order and return enabled skills keyed by name.

    On name collision, first-seen wins — so passing [system_dir, user_dir]
    makes system skills shadow user skills. Missing dirs are silently skipped.
    Skills with `disabled: true` or missing `name`/`description` are excluded.
    Skills with `hidden: true` are kept in the registry but flagged so that
    `build_skill_manifest` omits them from the agent's system prompt.

    When `allowlist` is given, the registry is filtered down to only those
    skill names (an absolute persona allowlist); names in the allowlist that
    match no discovered skill are logged as warnings. `None` leaves the
    registry unfiltered.
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
                portal_starter=fm.get("portal_starter", "").lower() in _TRUTHY,
                hidden=fm.get("hidden", "").lower() in _TRUTHY,
            )
    if allowlist is not None:
        for unknown in sorted(allowlist - registry.keys()):
            logger.warning(
                "persona allowlist names unknown skill '%s'", unknown
            )
        registry = {k: v for k, v in registry.items() if k in allowlist}
    return registry


def build_skill_manifest(
    skill_dirs: list[Path], allowlist: set[str] | None = None
) -> str:
    """Return markdown table of catalog skills (name + description).

    Skills flagged `hidden: true` are omitted — they stay in the registry
    (loadable, slash-forceable) but the agent won't route to them on its own.
    `allowlist` is forwarded to `load_registry` to scope the manifest to a
    persona's allowed skills.
    """
    registry = load_registry(skill_dirs, allowlist)
    catalog = [s for s in registry.values() if not s.hidden]
    if not catalog:
        logger.info("no catalog skills found in %s", [str(d) for d in skill_dirs])
        return ""

    logger.info("manifest lists %d skills: %s",
                len(catalog), ", ".join(s.name for s in catalog))

    lines = [
        "## Available Skills",
        "| Skill | When to Use |",
        "|-------|-------------|",
    ]
    for skill in sorted(catalog, key=lambda s: s.name):
        lines.append(f"| {skill.name} | {skill.description} |")
    return "\n".join(lines)


def _display_name(name: str) -> str:
    """Derive a user-facing label: 'investment-memo' -> 'Investment memo'."""
    words = name.replace("-", " ").replace("_", " ")
    return words[:1].upper() + words[1:]


def portal_skill_list(skill_dirs: list[Path]) -> list[dict]:
    """User-facing skills for the portal Skills panel.

    Returns skills that set `portal_summary`, sorted by name. Each entry:
    {name, display_name, summary, starter}.

    Two independent portal gates:
    - `portal_summary` — browse-panel gate. A skill appears in the Skills
      panel only if it sets `portal_summary`; `summary` is that value.
    - `portal_starter` — empty-page starter gate, surfaced as `starter`.
      The empty-state "What would you like to do?" rows filter on this.

    Starters are a subset of the browse panel: a skill that sets
    `portal_starter` without `portal_summary` is excluded entirely (and a
    warning is logged). `hidden` skills are excluded regardless.
    """
    registry = load_registry(skill_dirs)
    out = []
    for skill in registry.values():
        if skill.hidden:
            continue
        if not skill.portal_summary:
            if skill.portal_starter:
                logger.warning(
                    "skill '%s' sets portal_starter but lacks portal_summary; "
                    "excluded from portal",
                    skill.name,
                )
            continue
        out.append({
            "name": skill.name,
            "display_name": _display_name(skill.name),
            "summary": skill.portal_summary,
            "starter": skill.portal_starter,
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
