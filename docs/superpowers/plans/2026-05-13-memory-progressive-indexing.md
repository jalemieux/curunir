# Memory Progressive Indexing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing hourly memory-extraction coroutine so it also maintains two on-disk indexes — a chronological timeline of conversations and per-entity topic indexes — enabling progressive discovery (MEMORY.md → indexes → archives) without bloating the always-loaded MEMORY.md.

**Architecture:** A new `src/memory_indexer.py` exposes a single `update_indexes()` function. After `_extract()` writes facts and the conversation summary, it calls the indexer with the archive path, the set of memory files the conversation touched (taken directly from `fact["file"]` values), and the conversation slug. The indexer writes (a) `summaries/timeline.md` — a flat newest-first list — and (b) one file per touched entity at `summaries/topics/<slug>.md`. Both indexes use regex-based **upsert by archive path** so re-extracting the same session updates entries in place. MEMORY.md ships as a small static pointers file in `context.default/memory/` — it does not grow automatically.

**Tech Stack:** Python 3.12+, pytest + pytest-asyncio. No new dependencies. Pure synchronous file I/O for the indexer (called from the existing async extraction path; no async needed because writes are tiny and synchronous matches how `_write_fact` / `_write_summary` already work).

---

## Background — What Exists Today

Read these first so the implementation matches existing patterns:

- `src/memory_extractor.py` — `_extract()` orchestrates: call LLM → write facts via `_write_fact()` → write archive summary via `_write_summary()`. Both `_write_*` helpers return the `Path` they wrote.
- `_write_fact()` upserts by H2 heading using `_replace_section()`. The fact dict has shape `{"file": "people/anna.md", "content": "## Heading\n..."}`.
- `_write_summary()` writes to `archives/conversations/YYYY-MM-DD-<slug>.md`. If `archive_path` is passed (re-extraction of an ongoing session), it overwrites the same path.
- `_safe_path()` resolves a relative path under `memory_dir` and rejects traversal — reuse this for any user-derived paths.
- `tests/test_memory_extractor.py` — async tests using `agent_config` fixture from `tests/conftest.py`. LLM is mocked with `patch("src.memory_extractor.call_llm", new_callable=AsyncMock)`.
- **`MEMORY.md` is referenced in code but does not exist in `context.default/memory/` yet** — `_collect_existing_headings()` excludes it defensively. This plan creates it as a static pointers file.
- `context.default/memory/` is copied into `context/memory/` on first run by `bootstrap.py` (never overwriting). Updating defaults benefits new installs; existing installs are unaffected.

## Index Format (Reference for All Tasks)

**`summaries/timeline.md`** — one file, flat, newest first:

```markdown
# Conversation Timeline

Newest first. Each entry links to the archived conversation summary.

- 2026-05-13 — [memory-architecture](archives/conversations/2026-05-13-memory-architecture.md)
- 2026-05-12 — [mcp-research](archives/conversations/2026-05-12-mcp-research.md)
```

**`summaries/topics/<topic-slug>.md`** — one file per touched entity:

```markdown
# Topic: people-anna

Conversations that touched `people/anna.md`.

- 2026-05-13 — [memory-architecture](../../archives/conversations/2026-05-13-memory-architecture.md)
- 2026-05-01 — [annas-promotion](../../archives/conversations/2026-05-01-annas-promotion.md)
```

Note: topic-file links use `../../archives/...` because they live two levels deep. Helpers compute this from `archive_path.relative_to(memory_dir)` and the topic file's depth.

**Entry line format (canonical):** `- YYYY-MM-DD — [<slug>](<rel-path>)`

**Topic slug rule:** `people/anna.md` → `people-anna`; `projects.md` → `projects`. Implemented by joining `Path.parts` with `-` and dropping the `.md` suffix.

**Eligibility rule for topic indexing:** skip when the touched file is `README.md`, `MEMORY.md`, or lives under `archives/` or `summaries/`. Everything else is a valid topic anchor.

---

## File Structure

**New files:**
- `src/memory_indexer.py` — indexer module (pure functions, sync I/O)
- `tests/test_memory_indexer.py` — unit tests for indexer
- `context.default/memory/MEMORY.md` — static pointers file (always-loaded layer)

**Modified files:**
- `src/memory_extractor.py` — `_extract()` collects touched files and calls `update_indexes()`
- `tests/test_memory_extractor.py` — one integration test that verifies indexes are written end-to-end
- `context.default/memory/README.md` — point at MEMORY.md and the indexes in "Where to look first"
- `docs/architecture.md` — add memory-indexer component + progressive-discovery note + changelog entry

---

## Task 1: Skeleton + first failing test for the upsert helper

**Files:**
- Create: `src/memory_indexer.py`
- Create: `tests/test_memory_indexer.py`

The whole indexer is built on one regex-based upsert primitive. Build it first, test-first.

- [ ] **Step 1: Create empty module**

Create `src/memory_indexer.py` with this exact content:

```python
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
    raise NotImplementedError
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_memory_indexer.py`:

```python
from src.memory_indexer import _upsert_entry


def test_upsert_into_empty_body():
    text = "# Header\n\nIntro line.\n"
    line = "- 2026-05-13 — [slug](archives/conversations/2026-05-13-slug.md)"
    out = _upsert_entry(text, line, "archives/conversations/2026-05-13-slug.md")
    assert line in out
    assert out.endswith("\n")


def test_upsert_inserts_newest_first():
    text = (
        "# Header\n\n"
        "- 2026-05-10 — [old](archives/conversations/2026-05-10-old.md)\n"
    )
    line = "- 2026-05-13 — [new](archives/conversations/2026-05-13-new.md)"
    out = _upsert_entry(text, line, "archives/conversations/2026-05-13-new.md")
    new_idx = out.index("[new]")
    old_idx = out.index("[old]")
    assert new_idx < old_idx


def test_upsert_replaces_existing_entry_for_same_archive():
    rel = "archives/conversations/2026-05-13-slug.md"
    text = (
        "# Header\n\n"
        f"- 2026-05-13 — [old-slug]({rel})\n"
        "- 2026-05-10 — [other](archives/conversations/2026-05-10-other.md)\n"
    )
    line = f"- 2026-05-13 — [new-slug]({rel})"
    out = _upsert_entry(text, line, rel)
    assert "[new-slug]" in out
    assert "[old-slug]" not in out
    assert out.count(rel) == 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_memory_indexer.py -v`
Expected: 3 failures with `NotImplementedError`.

- [ ] **Step 4: Implement `_upsert_entry`**

Replace the `raise NotImplementedError` body with:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_memory_indexer.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/memory_indexer.py tests/test_memory_indexer.py
git commit -m "feat(memory): add upsert primitive for progressive-discovery indexes"
```

---

## Task 2: Topic slug + eligibility helpers

**Files:**
- Modify: `src/memory_indexer.py`
- Modify: `tests/test_memory_indexer.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_memory_indexer.py`:

```python
import pytest

from src.memory_indexer import _is_topic_eligible, _topic_slug_for


@pytest.mark.parametrize("rel,expected", [
    ("projects.md", "projects"),
    ("people/anna.md", "people-anna"),
    ("people/jane-doe.md", "people-jane-doe"),
    ("core-insights.md", "core-insights"),
])
def test_topic_slug_for(rel, expected):
    assert _topic_slug_for(rel) == expected


@pytest.mark.parametrize("rel,expected", [
    ("projects.md", True),
    ("people/anna.md", True),
    ("preferences.md", True),
    ("README.md", False),
    ("MEMORY.md", False),
    ("archives/conversations/2026-05-13-foo.md", False),
    ("summaries/timeline.md", False),
    ("summaries/topics/projects.md", False),
])
def test_is_topic_eligible(rel, expected):
    assert _is_topic_eligible(rel) is expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory_indexer.py -v`
Expected: failures with `ImportError` for the new names.

- [ ] **Step 3: Implement the helpers** — append to `src/memory_indexer.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_memory_indexer.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/memory_indexer.py tests/test_memory_indexer.py
git commit -m "feat(memory): add topic slug + eligibility helpers"
```

---

## Task 3: Timeline writer

**Files:**
- Modify: `src/memory_indexer.py`
- Modify: `tests/test_memory_indexer.py`

The timeline lives at `summaries/timeline.md`. Initialize with a header on first write; upsert thereafter.

- [ ] **Step 1: Write the failing tests** — append:

```python
from datetime import date

from src.memory_indexer import _update_timeline


def test_update_timeline_creates_file_with_header(tmp_path):
    archive = tmp_path / "archives" / "conversations" / "2026-05-13-foo.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# foo\n")

    _update_timeline(tmp_path, archive, "foo", date(2026, 5, 13))

    timeline = tmp_path / "summaries" / "timeline.md"
    assert timeline.exists()
    text = timeline.read_text()
    assert text.startswith("# Conversation Timeline")
    assert "- 2026-05-13 — [foo](archives/conversations/2026-05-13-foo.md)" in text


def test_update_timeline_inserts_newest_first(tmp_path):
    archive_dir = tmp_path / "archives" / "conversations"
    archive_dir.mkdir(parents=True)
    old = archive_dir / "2026-05-10-old.md"
    new = archive_dir / "2026-05-13-new.md"
    old.write_text("# old\n")
    new.write_text("# new\n")

    _update_timeline(tmp_path, old, "old", date(2026, 5, 10))
    _update_timeline(tmp_path, new, "new", date(2026, 5, 13))

    text = (tmp_path / "summaries" / "timeline.md").read_text()
    new_idx = text.index("[new]")
    old_idx = text.index("[old]")
    assert new_idx < old_idx


def test_update_timeline_upserts_same_archive(tmp_path):
    archive = tmp_path / "archives" / "conversations" / "2026-05-13-foo.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# foo\n")

    _update_timeline(tmp_path, archive, "first-slug", date(2026, 5, 13))
    _update_timeline(tmp_path, archive, "second-slug", date(2026, 5, 13))

    text = (tmp_path / "summaries" / "timeline.md").read_text()
    assert "[first-slug]" not in text
    assert "[second-slug]" in text
    assert text.count("2026-05-13-foo.md") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory_indexer.py -v`
Expected: ImportError on `_update_timeline`.

- [ ] **Step 3: Implement the writer** — append to `src/memory_indexer.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_memory_indexer.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/memory_indexer.py tests/test_memory_indexer.py
git commit -m "feat(memory): add timeline index writer"
```

---

## Task 4: Topic writer

**Files:**
- Modify: `src/memory_indexer.py`
- Modify: `tests/test_memory_indexer.py`

Topic files live at `summaries/topics/<slug>.md`. Links from a topic file to an archive go up two levels.

- [ ] **Step 1: Write the failing tests** — append:

```python
from src.memory_indexer import _update_topic


def test_update_topic_creates_file_with_header(tmp_path):
    archive = tmp_path / "archives" / "conversations" / "2026-05-13-foo.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# foo\n")

    _update_topic(tmp_path, "people/anna.md", archive, "foo", date(2026, 5, 13))

    topic = tmp_path / "summaries" / "topics" / "people-anna.md"
    assert topic.exists()
    text = topic.read_text()
    assert text.startswith("# Topic: people-anna")
    assert "`people/anna.md`" in text
    assert (
        "- 2026-05-13 — [foo](../../archives/conversations/2026-05-13-foo.md)"
        in text
    )


def test_update_topic_link_uses_two_level_relative(tmp_path):
    archive = tmp_path / "archives" / "conversations" / "2026-05-13-foo.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# foo\n")

    _update_topic(tmp_path, "projects.md", archive, "foo", date(2026, 5, 13))

    text = (tmp_path / "summaries" / "topics" / "projects.md").read_text()
    assert "../../archives/conversations/2026-05-13-foo.md" in text


def test_update_topic_upserts_same_archive(tmp_path):
    archive = tmp_path / "archives" / "conversations" / "2026-05-13-foo.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# foo\n")

    _update_topic(tmp_path, "projects.md", archive, "first", date(2026, 5, 13))
    _update_topic(tmp_path, "projects.md", archive, "second", date(2026, 5, 13))

    text = (tmp_path / "summaries" / "topics" / "projects.md").read_text()
    assert "[first]" not in text
    assert "[second]" in text
    assert text.count("2026-05-13-foo.md") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory_indexer.py -v`
Expected: ImportError on `_update_topic`.

- [ ] **Step 3: Implement the writer** — append to `src/memory_indexer.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_memory_indexer.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/memory_indexer.py tests/test_memory_indexer.py
git commit -m "feat(memory): add topic index writer"
```

---

## Task 5: `update_indexes` orchestrator

**Files:**
- Modify: `src/memory_indexer.py`
- Modify: `tests/test_memory_indexer.py`

Single entry point the extractor calls. Skips ineligible touched files; writes timeline once; writes one topic file per eligible touched entity.

- [ ] **Step 1: Write the failing tests** — append:

```python
from src.memory_indexer import update_indexes


def test_update_indexes_writes_timeline_and_topics(tmp_path):
    archive = tmp_path / "archives" / "conversations" / "2026-05-13-foo.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# foo\n")

    update_indexes(
        memory_dir=tmp_path,
        archive_path=archive,
        touched_files=["projects.md", "people/anna.md"],
        slug="foo",
        today=date(2026, 5, 13),
    )

    timeline = (tmp_path / "summaries" / "timeline.md").read_text()
    projects = (tmp_path / "summaries" / "topics" / "projects.md").read_text()
    anna = (tmp_path / "summaries" / "topics" / "people-anna.md").read_text()
    assert "[foo]" in timeline
    assert "[foo]" in projects
    assert "[foo]" in anna


def test_update_indexes_skips_ineligible_touched_files(tmp_path):
    archive = tmp_path / "archives" / "conversations" / "2026-05-13-foo.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# foo\n")

    update_indexes(
        memory_dir=tmp_path,
        archive_path=archive,
        touched_files=["README.md", "MEMORY.md", "archives/conversations/x.md", "projects.md"],
        slug="foo",
        today=date(2026, 5, 13),
    )

    topics_dir = tmp_path / "summaries" / "topics"
    written = sorted(p.name for p in topics_dir.iterdir())
    assert written == ["projects.md"]


def test_update_indexes_defaults_today_to_real_date(tmp_path):
    """today=None should fall back to date.today() without crashing."""
    archive = tmp_path / "archives" / "conversations" / "2026-05-13-foo.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# foo\n")

    update_indexes(
        memory_dir=tmp_path,
        archive_path=archive,
        touched_files=[],
        slug="foo",
    )

    assert (tmp_path / "summaries" / "timeline.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory_indexer.py -v`
Expected: ImportError on `update_indexes`.

- [ ] **Step 3: Implement the orchestrator** — append to `src/memory_indexer.py`:

```python
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
    _update_timeline(memory_dir, archive_path, slug, today)

    seen: set[str] = set()
    for rel in touched_files:
        if rel in seen or not _is_topic_eligible(rel):
            continue
        seen.add(rel)
        _update_topic(memory_dir, rel, archive_path, slug, today)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_memory_indexer.py -v`
Expected: all passed (10 tests total in this file).

- [ ] **Step 5: Commit**

```bash
git add src/memory_indexer.py tests/test_memory_indexer.py
git commit -m "feat(memory): add update_indexes orchestrator"
```

---

## Task 6: Wire indexer into `extract_learnings`

**Files:**
- Modify: `src/memory_extractor.py`
- Modify: `tests/test_memory_extractor.py`

`_extract()` already calls `_write_fact()` in a loop and then `_write_summary()`. Collect the set of `fact["file"]` values whose writes succeeded, and after summary success, call `update_indexes()`.

- [ ] **Step 1: Write the failing integration test** — append to `tests/test_memory_extractor.py`:

```python
@pytest.mark.asyncio
async def test_extract_writes_timeline_and_topic_indexes(agent_config):
    """End-to-end: extraction writes facts, summary, AND progressive-discovery indexes."""
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "README.md").write_text("# Memory\n")

    llm_response = LLMResponse(
        text=json.dumps({
            "facts": [
                {"file": "projects.md", "content": "## Curunir\n**Fact:** memory-indexing in flight"},
                {"file": "people/anna.md", "content": "## Role\n**Fact:** PM"},
            ],
            "summary": {
                "topic_slug": "memory-indexing",
                "content": "Discussed progressive discovery design.",
            },
        }),
        tool_calls=None,
    )

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, return_value=llm_response):
        archive = await extract_learnings(agent_config, _history(user_count=2))

    assert archive is not None
    assert archive.exists()

    timeline = (mem_dir / "summaries" / "timeline.md").read_text()
    assert "[memory-indexing]" in timeline

    projects_topic = (mem_dir / "summaries" / "topics" / "projects.md").read_text()
    anna_topic = (mem_dir / "summaries" / "topics" / "people-anna.md").read_text()
    assert "[memory-indexing]" in projects_topic
    assert "[memory-indexing]" in anna_topic


@pytest.mark.asyncio
async def test_extract_skips_indexes_when_no_summary(agent_config):
    """If the LLM returns no summary, no archive or indexes are written."""
    mem_dir = agent_config.context_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "README.md").write_text("# Memory\n")

    llm_response = LLMResponse(
        text=json.dumps({"facts": [], "summary": None}),
        tool_calls=None,
    )

    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock, return_value=llm_response):
        result = await extract_learnings(agent_config, _history(user_count=2))

    assert result is None
    assert not (mem_dir / "summaries").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory_extractor.py -v -k "indexes or no_summary"`
Expected: 2 failures — indexes not written.

- [ ] **Step 3: Modify `_extract` in `src/memory_extractor.py`**

At the top of the file, add the import (after the existing `from .skills import load_skill` line):

```python
from .memory_indexer import update_indexes
```

Replace the tail of `_extract()` — currently lines that look like:

```python
    # Write facts
    for fact in data.get("facts", []):
        _write_fact(memory_dir, fact)

    # Write conversation summary
    summary = data.get("summary")
    if summary:
        return _write_summary(memory_dir, summary, archive_path=archive_path)
    return None
```

with:

```python
    # Write facts and track which entities they touched
    touched_files: list[str] = []
    for fact in data.get("facts", []):
        if _write_fact(memory_dir, fact) is not None:
            file_rel = fact.get("file")
            if file_rel:
                touched_files.append(file_rel)

    # Write conversation summary, then update progressive-discovery indexes
    summary = data.get("summary")
    if not summary:
        return None

    archive = _write_summary(memory_dir, summary, archive_path=archive_path)
    if archive is not None:
        try:
            update_indexes(
                memory_dir=memory_dir,
                archive_path=archive,
                touched_files=touched_files,
                slug=summary.get("topic_slug", "misc"),
            )
        except Exception:
            log.exception("memory index update failed")
    return archive
```

The `try/except` mirrors the top-level guard in `extract_learnings()` — index failures must not break archive writes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_memory_extractor.py -v`
Expected: all passed (existing tests plus the 2 new ones).

- [ ] **Step 5: Commit**

```bash
git add src/memory_extractor.py tests/test_memory_extractor.py
git commit -m "feat(memory): update indexes after archive summary write"
```

---

## Task 7: Default `MEMORY.md` + README pointers

**Files:**
- Create: `context.default/memory/MEMORY.md`
- Modify: `context.default/memory/README.md`

MEMORY.md is the always-loaded routing table. It must stay small and stable — no auto-generated content.

- [ ] **Step 1: Create `context.default/memory/MEMORY.md`**

```markdown
# Memory Index

Always-loaded routing table. Stays small. Points to indexes that grow.

## Where to find things

| Layer | File / dir | When to load |
|---|---|---|
| Taxonomy | `README.md` | Always (this is the index of indexes) |
| Owner facts | `preferences.md`, `projects.md`, `tasks.md`, `people/`, `core-insights.md` | On owner-related questions |
| Chronological history | `summaries/timeline.md` | "What did we discuss recently?" / "When did X happen?" |
| Topic history | `summaries/topics/<slug>.md` | "What have we discussed about X?" — slug matches a memory file (`projects`, `people-anna`, etc.) |
| Full conversation summaries | `archives/conversations/YYYY-MM-DD-<slug>.md` | When an index entry isn't detailed enough |

## Progressive discovery

```
README/MEMORY ──> summaries/timeline.md ──> archives/conversations/*.md
            └──> summaries/topics/<slug>.md ──┘
```

Read in this order: index → topic-or-timeline → archive. Don't load
`archives/` wholesale — grep or follow links from the indexes.

## Maintenance

`summaries/timeline.md` and `summaries/topics/*.md` are written automatically
by the memory-extraction background job. **Do not hand-edit them** — they will
be regenerated. Hand-edit the topical files (`preferences.md`, etc.) and
`README.md` instead.
```

- [ ] **Step 2: Update `context.default/memory/README.md`**

Replace the "Where to look first" section (lines 11-22 — the numbered list and the "single read" paragraph) with:

```markdown
## Where to look first

When the user asks "who am I?", "what do you know about me?", or anything that
requires knowing the owner — **always** read these in order before responding:

1. `MEMORY.md` — routing table; points at the right index for the question
2. `preferences.md` — name, age, family, role, working style, tool preferences, communication preferences
3. `projects.md` — current projects, status, architecture, relationships between them
4. `tasks.md` — open items / unresolved questions the user is working through
5. `people/*.md` — colleagues, collaborators, stakeholders the user works with

For questions about past conversations ("what did we discuss about X?"), go through
the indexes instead: `summaries/topics/<slug>.md` (if X matches a memory file) or
`summaries/timeline.md` (if you're orienting by time).

A single read on this README is **not** enough. Read the files above before
saying "I don't know who you are."
```

Then update the Taxonomy table — add three rows after the existing `archives/...` row:

```markdown
| `MEMORY.md` | Small always-loaded routing table pointing at indexes |
| `summaries/timeline.md` | Auto-maintained chronological list of all archived conversations |
| `summaries/topics/<slug>.md` | Auto-maintained: archives that touched the entity named by `<slug>` |
```

- [ ] **Step 3: Verify default content parses (sanity smoke)**

Run: `python -c "from pathlib import Path; print((Path('context.default/memory/MEMORY.md')).read_text()[:80])"`
Expected: Prints the first 80 chars of MEMORY.md without error.

- [ ] **Step 4: Commit**

```bash
git add context.default/memory/MEMORY.md context.default/memory/README.md
git commit -m "docs(memory): add MEMORY.md routing table and document index layout"
```

---

## Task 8: Manual end-to-end smoke

No automated test — just a quick check that a real extraction run produces the expected files.

- [ ] **Step 1: Run a fast smoke against a temp context**

```bash
python -c "
import asyncio, json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from src.config import AgentConfig
from src.llm import LLMResponse
from src.memory_extractor import extract_learnings

async def main():
    ctx = Path('/tmp/curunir-smoke')
    ctx.mkdir(exist_ok=True)
    (ctx / 'identity.md').write_text('test')
    (ctx / 'memory').mkdir(exist_ok=True)
    (ctx / 'memory' / 'README.md').write_text('# Memory\n')

    cfg = AgentConfig(identity_file=ctx/'identity.md', context_dir=ctx, skill_dirs=[])

    resp = LLMResponse(text=json.dumps({
        'facts': [{'file': 'projects.md', 'content': '## Curunir\n**Fact:** indexing'}],
        'summary': {'topic_slug': 'smoke', 'content': 'A smoke test.'}
    }), tool_calls=None)

    hist = [{'role':'user','content':'a'},{'role':'assistant','content':'b'},{'role':'user','content':'c'}]

    with patch('src.memory_extractor.call_llm', new_callable=AsyncMock, return_value=resp):
        out = await extract_learnings(cfg, hist)
    print('archive:', out)
    print('timeline:', (ctx/'memory'/'summaries'/'timeline.md').read_text())
    print('topic:', (ctx/'memory'/'summaries'/'topics'/'projects.md').read_text())

asyncio.run(main())
"
```

Expected: prints non-None archive path, timeline content containing `[smoke]`, topic content containing `[smoke]`.

- [ ] **Step 2: Clean up smoke dir**

```bash
rm -rf /tmp/curunir-smoke
```

- [ ] **Step 3: Run the full test suite once**

Run: `pytest tests/ -x`
Expected: all passed. If anything broke in `test_memory_extractor.py`, the wiring in Task 6 is the suspect — most likely a touched-file collection bug.

---

## Task 9: Documentation

**Files:**
- Modify: `docs/architecture.md`

CLAUDE.md requires updating `docs/architecture.md` after new components. Add one component-table row and one changelog entry. Keep it short.

- [ ] **Step 1: Read the file's existing structure**

Run: `grep -n "^##" docs/architecture.md | head -20`

Identify (a) the components table and (b) the changelog section.

- [ ] **Step 2: Add a row to the components table**

In the components table, after the row mentioning `memory_extractor.py`, insert:

```markdown
| `src/memory_indexer.py` | Maintains `summaries/timeline.md` and `summaries/topics/*.md`. Called by `memory_extractor` after each archive write. Pure sync I/O; failures are logged and swallowed so they cannot break extraction. |
```

- [ ] **Step 3: Add a changelog entry at the bottom**

Append under the changelog section:

```markdown
- **2026-05-13** — Progressive-discovery memory indexes. `memory_extractor` now also updates `summaries/timeline.md` (chronological, newest-first) and `summaries/topics/<slug>.md` (one per touched entity) on every archive write. MEMORY.md added as a small static routing table in `context.default/memory/`. See `src/memory_indexer.py`.
```

- [ ] **Step 4: Commit**

```bash
git add docs/architecture.md
git commit -m "docs: add memory-indexer component + progressive-discovery changelog"
```

---

## Out of scope (intentionally)

These are real follow-ups, **not part of this plan**:

- **On-demand topic synthesis skill** (the second half of PR #106). A user-invoked `consolidate topic X` skill that reads matching archives and produces a deeper synthesis doc is a separate piece. It can be built on top of these indexes once they're populated. Do not build it here.
- **Auto-pruning / consolidation of old timeline entries.** Timeline grows monotonically. If/when it gets too large to load on demand, add monthly archival (e.g., split into `timeline-2026.md`, `timeline-2025.md`). Not needed at current archive counts.
- **Backfill** of existing archives into the indexes. New installs start clean; existing installs without populated indexes simply build them up over the next few extraction cycles. If a user wants an immediate backfill, that's a one-shot script — out of scope.
- **Migration of PR #106**. This plan supersedes its periodic-cron-skill approach. Close that PR with a comment pointing to this plan and the indexer module; keep its `docs/research/claude-code-dream-analysis.md` deliverable if reviewers still want it (independent doc).
