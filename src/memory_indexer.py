"""Maintains progressive-discovery indexes over conversation archives.

Called from src.memory_extractor._extract after the archive summary is written.
Writes:
  - summaries/timeline.md            (flat, newest-first, all archives)
  - summaries/topics/<slug>.md       (one per touched memory entity)

Both indexes upsert by archive path: re-extracting the same session updates
the existing entry in place rather than appending a duplicate.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path


def _upsert_entry(text: str, new_line: str, archive_rel: str) -> str:
    """Return text with an entry for archive_rel set to new_line.

    Rules:
      - If a line referencing archive_rel exists, replace it in place.
      - Else insert new_line before the first existing dated entry (newest first).
      - Else (no entries yet) append at end.
    """
    pattern = re.compile(
        rf"^- \d{{4}}-\d{{2}}-\d{{2}} — \[.*?\]\({re.escape(archive_rel)}\)\s*$",
        re.MULTILINE,
    )
    if pattern.search(text):
        return pattern.sub(new_line.rstrip(), text, count=1)

    lines = text.splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if re.match(r"^- \d{4}-\d{2}-\d{2} — ", ln):
            return "".join(lines[:i]) + new_line.rstrip() + "\n" + "".join(lines[i:])

    suffix = "" if text.endswith("\n") else "\n"
    return text + suffix + new_line.rstrip() + "\n"


_INDEX_EXCLUDE_FILES = {"README.md", "MEMORY.md"}
_INDEX_EXCLUDE_DIRS = {"archives", "summaries"}


def _topic_slug_for(rel_path: str) -> str:
    """Convert 'people/anna.md' -> 'people-anna', 'projects.md' -> 'projects'."""
    p = Path(rel_path)
    parts = list(p.parts[:-1]) + [p.stem]
    return "-".join(parts)


def _is_topic_eligible(rel_path: str) -> bool:
    """Topic indexes anchor to user-managed entities only.

    Skip: README.md, MEMORY.md, anything under archives/ or summaries/.
    """
    p = Path(rel_path)
    if not p.parts:
        return False
    if p.parts[0] in _INDEX_EXCLUDE_DIRS:
        return False
    if p.name in _INDEX_EXCLUDE_FILES:
        return False
    return True


_TIMELINE_HEADER = (
    "# Conversation Timeline\n\n"
    "Newest first. Each entry links to the archived conversation summary.\n\n"
)


def _entry_line(today: date, slug: str, archive_rel: str) -> str:
    return f"- {today.isoformat()} — [{slug}]({archive_rel})"


def _update_timeline(
    memory_dir: Path,
    archive_path: Path,
    slug: str,
    today: date,
) -> None:
    timeline = memory_dir / "summaries" / "timeline.md"
    timeline.parent.mkdir(parents=True, exist_ok=True)
    archive_rel = archive_path.relative_to(memory_dir).as_posix()
    line = _entry_line(today, slug, archive_rel)

    if not timeline.exists():
        timeline.write_text(_TIMELINE_HEADER + line + "\n")
        return

    text = timeline.read_text()
    timeline.write_text(_upsert_entry(text, line, archive_rel))


def _topic_relative_archive(archive_path: Path, memory_dir: Path) -> str:
    """Path from summaries/topics/ up to the archive file (always two levels up)."""
    archive_rel = archive_path.relative_to(memory_dir).as_posix()
    return "../../" + archive_rel


def _update_topic(
    memory_dir: Path,
    entity_rel: str,
    archive_path: Path,
    slug: str,
    today: date,
) -> None:
    topic_slug = _topic_slug_for(entity_rel)
    target = memory_dir / "summaries" / "topics" / f"{topic_slug}.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    archive_link = _topic_relative_archive(archive_path, memory_dir)
    line = _entry_line(today, slug, archive_link)

    if not target.exists():
        header = (
            f"# Topic: {topic_slug}\n\n"
            f"Conversations that touched `{entity_rel}`.\n\n"
        )
        target.write_text(header + line + "\n")
        return

    text = target.read_text()
    target.write_text(_upsert_entry(text, line, archive_link))


def update_indexes(
    memory_dir: Path,
    archive_path: Path,
    touched_files: list[str],
    slug: str,
    *,
    today: date | None = None,
) -> None:
    """Update timeline and per-entity topic indexes after an archive is written.

    Args:
        memory_dir: The context/memory directory.
        archive_path: Absolute path to the archive file just written.
        touched_files: Memory-relative paths of files this conversation wrote facts to.
        slug: Conversation slug (used as the link text in index entries).
        today: Override for date.today() — for testing.
    """
    today = today or date.today()
    memory_dir = memory_dir.resolve()
    archive_path = archive_path.resolve()
    _update_timeline(memory_dir, archive_path, slug, today)

    seen: set[str] = set()
    for rel in touched_files:
        if rel in seen or not _is_topic_eligible(rel):
            continue
        seen.add(rel)
        _update_topic(memory_dir, rel, archive_path, slug, today)
