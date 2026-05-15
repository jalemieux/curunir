import pytest
from datetime import date

from src.memory_indexer import _is_topic_eligible, _topic_slug_for, _update_timeline, _upsert_entry, _update_topic, update_indexes


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
    ("archives/conversations/2026-05-13-foo.md", False),
    ("summaries/timeline.md", False),
    ("summaries/topics/projects.md", False),
])
def test_is_topic_eligible(rel, expected):
    assert _is_topic_eligible(rel) is expected


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
        touched_files=["README.md", "archives/conversations/x.md", "projects.md"],
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


def test_update_indexes_handles_symlinked_memory_dir(tmp_path):
    """Regression: memory_dir and archive_path with different symlink resolutions must not break indexing.

    On macOS /tmp is a symlink to /private/tmp; production callers can pass an
    unresolved memory_dir while archive_path comes back resolved from _safe_path.
    """
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir)

    # archive_path uses the real (resolved) location
    archive = real_dir / "archives" / "conversations" / "2026-05-13-foo.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# foo\n")

    # memory_dir uses the symlinked (unresolved) location — different string,
    # same underlying directory. relative_to() would fail without normalization.
    update_indexes(
        memory_dir=link_dir,
        archive_path=archive,
        touched_files=["projects.md"],
        slug="foo",
        today=date(2026, 5, 13),
    )

    assert (real_dir / "summaries" / "timeline.md").exists()
    assert (real_dir / "summaries" / "topics" / "projects.md").exists()
