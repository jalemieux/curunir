"""One-shot CLI: migrate context/memory/ to the post-#102 layout.

Before #102, `preferences.md` held a mix of owner identity facts (name,
family, role) and working-style preferences. The new layout splits these:

    profile.md      Owner identity facts (name, pronouns, family, role,
                    addresses, medical notes — about the user).
    preferences.md  Owner working/communication style (response length,
                    citation conventions, consent boundaries, tool prefs —
                    about how the user wants to work).
    core-insights.md Curunir's own accumulated realizations (untouched).

This script splits an existing `context/memory/preferences.md` into the two
new files without data loss:

1. Parses the source file into `## Topic` sections plus any preamble.
2. Asks the LLM to *classify* each section as "profile" or "preferences" —
   the LLM never rewrites content, only emits a class for each heading, so
   the section text is preserved verbatim.
3. Validates that every section is classified exactly once and that all
   classified section text is present byte-for-byte in the original input
   (defensive check against accidental loss).
4. Backs up the original `preferences.md` to `preferences.md.bak.<ts>`
   before mutating anything.
5. Writes the new `profile.md` and rewrites `preferences.md`.

Usage:
    python scripts/migrate_memory_layout.py                # apply
    python scripts/migrate_memory_layout.py --dry-run      # preview only
    python scripts/migrate_memory_layout.py --memory-dir context/memory --force

The script is idempotent: if `profile.md` already exists it exits without
doing anything unless `--force` is passed.
"""

import argparse
import asyncio
import datetime as _dt
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

from src.config import AgentConfig
from src.llm import call_llm

PROFILE_HEADER = """\
<!--
Owner identity facts only — name, pronouns, family, role, professional
background, contact, addresses, medical notes. Anything that would still be
true in six months and is **about the user**.

Working style and communication preferences live in `preferences.md`.
Curunir's own personality and self-image live in `../identity.md`.
Curunir's accumulated realizations about itself live in `core-insights.md`.
-->

# Owner Profile
"""

PREFERENCES_HEADER = """\
<!--
Owner's working and communication preferences only — response length,
citation conventions, consent boundaries, tool preferences.

Owner factual data (name, family, role, addresses) lives in `profile.md`.
Curunir's own voice and personality live in `../identity.md`.
-->

# Owner Preferences
"""

CLASSIFY_PROMPT = """\
You are a classification system for memory sections in a personal-assistant codebase.

Each input section is a markdown block starting with a `## ` heading. Classify each section into exactly one of these two buckets:

- "profile" — facts about the user as a person: name, pronouns, age, family, role, professional background, addresses, contact info, timezone, medical notes, anything that would still be true in six months and is **about the user**.
- "preferences" — how the user wants the assistant to work: response length and style, citation conventions, tone shifts, consent boundaries, tool/workflow preferences, recurring tasks the user wants help with.

If a section is ambiguous (could fit either bucket), prefer "preferences".

Output ONLY a JSON array, one object per section, in the input order:

    [{"index": 0, "class": "profile"}, {"index": 1, "class": "preferences"}, ...]

No commentary, no code fences, no extra fields. Every input section index must appear exactly once.
"""


@dataclass
class Section:
    index: int
    heading: str  # the full "## Heading line" including the prefix
    body: str  # text after the heading, up to the next heading or EOF
    raw: str  # heading + body, byte-exact slice of the source

    @property
    def classified_text(self) -> str:
        return self.raw


def _split_sections(text: str) -> tuple[str, list[Section]]:
    """Split markdown into a preamble plus a list of `## ` sections.

    Preamble is everything before the first `## ` heading. Sections are
    delimited by `## ` at the start of a line. Section.raw is a byte-exact
    slice of the source so we can validate no content was dropped.
    """
    pattern = re.compile(r"^## ", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if not matches:
        return text, []

    preamble = text[: matches[0].start()]
    sections: list[Section] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw = text[start:end]
        # heading is the first line, body is the rest
        nl = raw.find("\n")
        if nl == -1:
            heading, body = raw, ""
        else:
            heading, body = raw[:nl], raw[nl + 1 :]
        sections.append(Section(index=i, heading=heading, body=body, raw=raw))
    return preamble, sections


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def _parse_classification(raw: str, n_sections: int) -> dict[int, str]:
    """Parse the LLM JSON output. Validate every index appears exactly once."""
    cleaned = _strip_fences(raw)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM did not return valid JSON: {e}\nGot:\n{cleaned}") from e

    if not isinstance(parsed, list):
        raise ValueError(f"Expected a JSON array, got {type(parsed).__name__}")

    result: dict[int, str] = {}
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError(f"Expected object entries, got {item!r}")
        idx = item.get("index")
        cls = item.get("class")
        if not isinstance(idx, int) or cls not in ("profile", "preferences"):
            raise ValueError(f"Invalid entry: {item!r}")
        if idx in result:
            raise ValueError(f"Duplicate classification for index {idx}")
        if idx < 0 or idx >= n_sections:
            raise ValueError(f"Index {idx} out of range [0, {n_sections})")
        result[idx] = cls

    missing = [i for i in range(n_sections) if i not in result]
    if missing:
        raise ValueError(f"Missing classification for indices: {missing}")
    return result


def _build_classification_payload(sections: list[Section]) -> str:
    chunks = []
    for s in sections:
        chunks.append(f"--- section index={s.index} ---\n{s.raw.rstrip()}\n")
    return "\n".join(chunks)


def _assemble(
    header: str,
    preamble: str,
    sections: list[Section],
) -> str:
    """Combine header + (optional) preamble + section blocks into the output text."""
    parts = [header.rstrip() + "\n"]
    if preamble.strip():
        parts.append("\n" + preamble.rstrip() + "\n")
    for s in sections:
        parts.append("\n" + s.raw.rstrip() + "\n")
    out = "".join(parts)
    if not out.endswith("\n"):
        out += "\n"
    return out


def _verify_no_loss(
    original_sections: list[Section],
    profile_text: str,
    preferences_text: str,
) -> None:
    """Every section's raw text must appear (byte-stripped) in exactly one output."""
    for s in original_sections:
        snippet = s.raw.strip()
        in_profile = snippet in profile_text
        in_prefs = snippet in preferences_text
        if not (in_profile ^ in_prefs):
            where = (
                "neither output"
                if not (in_profile or in_prefs)
                else "both outputs"
            )
            raise RuntimeError(
                f"Data-loss check failed: section index={s.index} "
                f"({s.heading!r}) ended up in {where}."
            )


async def migrate(
    memory_dir: Path,
    config: AgentConfig,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """Perform the migration. Returns a summary dict for the CLI."""
    prefs_path = memory_dir / "preferences.md"
    profile_path = memory_dir / "profile.md"

    if not memory_dir.is_dir():
        raise FileNotFoundError(f"Memory directory not found: {memory_dir}")

    if not prefs_path.exists():
        return {
            "status": "nothing-to-migrate",
            "reason": f"{prefs_path} does not exist; new install will be seeded by bootstrap.",
        }

    if profile_path.exists() and not force:
        return {
            "status": "already-migrated",
            "reason": f"{profile_path} already exists. Pass --force to re-run.",
        }

    original = prefs_path.read_text()
    preamble, sections = _split_sections(original)

    if not sections:
        return {
            "status": "no-sections",
            "reason": (
                f"{prefs_path} has no `## ` sections — nothing to split. "
                "Add `## ` headings to your facts, or move content manually."
            ),
        }

    payload = _build_classification_payload(sections)
    response = await call_llm(
        config.model,
        [
            {"role": "system", "content": CLASSIFY_PROMPT},
            {"role": "user", "content": payload},
        ],
        tools=[],
        api_base=config.api_base,
        openrouter_provider=config.openrouter_provider,
    )
    classification = _parse_classification(response.text or "", len(sections))

    profile_sections = [s for s in sections if classification[s.index] == "profile"]
    preferences_sections = [
        s for s in sections if classification[s.index] == "preferences"
    ]

    # Original preamble (text before the first `## `) stays with preferences.md,
    # its historical home. profile.md gets only its standard header.
    profile_text = _assemble(PROFILE_HEADER, preamble="", sections=profile_sections)
    preferences_text = _assemble(
        PREFERENCES_HEADER, preamble=preamble, sections=preferences_sections
    )

    _verify_no_loss(sections, profile_text, preferences_text)

    summary = {
        "status": "ok",
        "source": str(prefs_path),
        "profile_sections": [s.heading for s in profile_sections],
        "preferences_sections": [s.heading for s in preferences_sections],
        "profile_path": str(profile_path),
        "preferences_path": str(prefs_path),
        "backup_path": None,
        "dry_run": dry_run,
    }

    if dry_run:
        summary["profile_text"] = profile_text
        summary["preferences_text"] = preferences_text
        return summary

    timestamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = prefs_path.with_suffix(f".md.bak.{timestamp}")
    backup_path.write_text(original)
    summary["backup_path"] = str(backup_path)

    profile_path.write_text(profile_text)
    prefs_path.write_text(preferences_text)
    return summary


def _build_config() -> AgentConfig:
    kwargs: dict = {}
    if model := os.environ.get("MODEL"):
        kwargs["model"] = model
    if api_base := os.environ.get("API_BASE"):
        kwargs["api_base"] = api_base
    if provider := os.environ.get("OPENROUTER_PROVIDER"):
        kwargs["openrouter_provider"] = provider
    return AgentConfig(**kwargs)


def _print_summary(summary: dict) -> None:
    status = summary["status"]
    if status == "nothing-to-migrate":
        print(f"Nothing to migrate: {summary['reason']}")
        return
    if status == "already-migrated":
        print(f"Already migrated: {summary['reason']}")
        return
    if status == "no-sections":
        print(f"Cannot migrate: {summary['reason']}")
        return

    print("Migration plan:")
    print(f"  source:      {summary['source']}")
    print(f"  profile.md:  {len(summary['profile_sections'])} sections")
    for h in summary["profile_sections"]:
        print(f"      {h}")
    print(f"  preferences.md: {len(summary['preferences_sections'])} sections")
    for h in summary["preferences_sections"]:
        print(f"      {h}")
    if summary["dry_run"]:
        print("\n--- profile.md (dry-run) ---")
        print(summary["profile_text"])
        print("--- preferences.md (dry-run) ---")
        print(summary["preferences_text"])
        print("(dry run — no files written)")
    else:
        print(f"\n  backup:      {summary['backup_path']}")
        print(f"  wrote:       {summary['profile_path']}")
        print(f"  wrote:       {summary['preferences_path']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--memory-dir",
        default="context/memory",
        help="Memory directory (default: context/memory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned split without modifying any files.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run migration even if profile.md already exists.",
    )
    args = parser.parse_args(argv)

    load_dotenv()

    memory_dir = Path(args.memory_dir)
    config = _build_config()

    try:
        summary = asyncio.run(
            migrate(memory_dir, config, dry_run=args.dry_run, force=args.force)
        )
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f"Migration failed: {e}", file=sys.stderr)
        return 1

    _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
