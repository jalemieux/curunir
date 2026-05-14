import pytest

from src.memory_indexer import _is_topic_eligible, _topic_slug_for, _upsert_entry


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
    ("MEMORY.md", False),
    ("archives/conversations/2026-05-13-foo.md", False),
    ("summaries/timeline.md", False),
    ("summaries/topics/projects.md", False),
])
def test_is_topic_eligible(rel, expected):
    assert _is_topic_eligible(rel) is expected
