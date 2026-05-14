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
