"""Tests for portal.memos: Memo dataclass and MemoRepository."""
from datetime import date
from pathlib import Path

import pytest

from portal.memos import Memo, MemoRepository


def _write_memo(
    root: Path,
    slug: str,
    *,
    title: str = "Sample Memo",
    iso_date: str = "2026-05-24",
    category: str = "Equities",
    category_slug: str = "equities",
    dek: str = "A sample dek.",
    pages: int | None = 12,
    sources: int | None = 9,
    body: str = "## Heading\n\nA paragraph.",
    write_pdf: bool = True,
    pdf_name: str = "memo.pdf",
) -> Path:
    """Create a memo folder under root and return its path."""
    folder = root / slug
    folder.mkdir(parents=True, exist_ok=True)
    fm_lines = [
        "---",
        f'title: "{title}"',
        f"date: {iso_date}",
        f'category: "{category}"',
        f'category_slug: "{category_slug}"',
        f'dek: "{dek}"',
        f"pdf: {pdf_name}",
    ]
    if pages is not None:
        fm_lines.append(f"pages: {pages}")
    if sources is not None:
        fm_lines.append(f"sources: {sources}")
    fm_lines += ["---", "", body]
    (folder / "memo.md").write_text("\n".join(fm_lines))
    if write_pdf:
        (folder / pdf_name).write_bytes(b"%PDF-1.4\n%fake pdf\n")
    return folder


def test_repository_parses_a_memo(tmp_path: Path):
    _write_memo(tmp_path, "spacex-ipo-2026-05-24")

    repo = MemoRepository(tmp_path)
    memos = repo.list()

    assert len(memos) == 1
    memo = memos[0]
    assert memo.slug == "spacex-ipo-2026-05-24"
    assert memo.title == "Sample Memo"
    assert memo.date == date(2026, 5, 24)
    assert memo.category == "Equities"
    assert memo.category_slug == "equities"
    assert memo.dek == "A sample dek."
    assert memo.pages == 12
    assert memo.sources == 9
    assert memo.pdf_filename == "memo.pdf"
    assert memo.pdf_exists is True
    assert "<h2>Heading</h2>" in memo.body_html


def test_repository_lists_memos_newest_first(tmp_path: Path):
    _write_memo(tmp_path, "older", iso_date="2026-04-01", title="Older")
    _write_memo(tmp_path, "newer", iso_date="2026-05-24", title="Newer")
    _write_memo(tmp_path, "middle", iso_date="2026-05-01", title="Middle")

    repo = MemoRepository(tmp_path)
    titles = [m.title for m in repo.list()]

    assert titles == ["Newer", "Middle", "Older"]


def test_repository_get_by_slug(tmp_path: Path):
    _write_memo(tmp_path, "spacex-ipo-2026-05-24")

    repo = MemoRepository(tmp_path)
    memo = repo.get("spacex-ipo-2026-05-24")

    assert memo is not None
    assert memo.slug == "spacex-ipo-2026-05-24"


def test_repository_get_unknown_returns_none(tmp_path: Path):
    repo = MemoRepository(tmp_path)
    assert repo.get("does-not-exist") is None


def test_repository_categories_only_returns_used(tmp_path: Path):
    _write_memo(tmp_path, "a", category="Equities", category_slug="equities")
    _write_memo(tmp_path, "b", category="Equities", category_slug="equities")
    _write_memo(tmp_path, "c", category="Commodities", category_slug="commodities")

    repo = MemoRepository(tmp_path)
    cats = repo.categories()

    # Returns (slug, label) pairs sorted by label for stable ordering.
    assert cats == [
        ("commodities", "Commodities"),
        ("equities", "Equities"),
    ]


def test_repository_handles_missing_pdf(tmp_path: Path):
    _write_memo(tmp_path, "no-pdf", write_pdf=False)

    repo = MemoRepository(tmp_path)
    memo = repo.get("no-pdf")

    assert memo is not None
    assert memo.pdf_exists is False


def test_repository_skips_folder_without_memo_md(tmp_path: Path):
    (tmp_path / "empty-folder").mkdir()
    _write_memo(tmp_path, "valid")

    repo = MemoRepository(tmp_path)
    assert len(repo.list()) == 1
    assert repo.list()[0].slug == "valid"


def test_repository_skips_malformed_frontmatter(tmp_path: Path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "memo.md").write_text("---\ntitle: unclosed\n")  # no closing ---
    _write_memo(tmp_path, "good")

    repo = MemoRepository(tmp_path)
    slugs = [m.slug for m in repo.list()]
    assert slugs == ["good"]  # bad memo silently dropped, good one survives


def test_repository_skips_memo_missing_required_fields(tmp_path: Path):
    bad = tmp_path / "missing-title"
    bad.mkdir()
    (bad / "memo.md").write_text(
        "---\n"
        "date: 2026-05-24\n"
        'category: "X"\n'
        'category_slug: "x"\n'
        'dek: "d"\n'
        "pdf: memo.pdf\n"
        "---\n"
    )
    _write_memo(tmp_path, "good")

    repo = MemoRepository(tmp_path)
    assert [m.slug for m in repo.list()] == ["good"]


def test_repository_renders_pull_quote(tmp_path: Path):
    body = (
        "First paragraph.\n\n"
        "> [!pull]\n"
        "> The line nobody priced.\n\n"
        "Last paragraph."
    )
    _write_memo(tmp_path, "pull", body=body)

    repo = MemoRepository(tmp_path)
    html = repo.get("pull").body_html

    assert '<blockquote class="pull">' in html
    assert "The line nobody priced." in html
    # The marker itself should not appear in output.
    assert "[!pull]" not in html


def test_repository_reload_picks_up_new_memos(tmp_path: Path):
    _write_memo(tmp_path, "one")
    repo = MemoRepository(tmp_path)
    assert len(repo.list()) == 1

    _write_memo(tmp_path, "two", iso_date="2026-06-01")
    repo.reload()

    assert len(repo.list()) == 2


def test_repository_empty_dir(tmp_path: Path):
    repo = MemoRepository(tmp_path)
    assert repo.list() == []
    assert repo.categories() == []


def test_repository_handles_nonexistent_dir(tmp_path: Path):
    """A content dir that doesn't exist should produce an empty repo, not crash."""
    repo = MemoRepository(tmp_path / "does-not-exist")
    assert repo.list() == []
