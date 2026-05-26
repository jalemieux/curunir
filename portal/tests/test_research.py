"""Route tests for the public /research section."""
from datetime import date
from pathlib import Path
from urllib.parse import quote

import pytest

from portal import research
from portal.memos import MemoRepository


# Re-use the writer helper shape from test_memos.
def _write_memo(
    root: Path,
    slug: str,
    *,
    title: str = "Sample",
    iso_date: str = "2026-05-24",
    category: str = "Equities",
    category_slug: str = "equities",
    dek: str = "A dek.",
    pages: int | None = 12,
    sources: int | None = 9,
    body: str = "## H\n\nA paragraph.",
    write_pdf: bool = True,
    pdf_bytes: bytes = b"%PDF-1.4\nfake\n",
) -> Path:
    folder = root / slug
    folder.mkdir(parents=True, exist_ok=True)
    fm = [
        "---",
        f'title: "{title}"',
        f"date: {iso_date}",
        f'category: "{category}"',
        f'category_slug: "{category_slug}"',
        f'dek: "{dek}"',
        "pdf: memo.pdf",
    ]
    if pages is not None:
        fm.append(f"pages: {pages}")
    if sources is not None:
        fm.append(f"sources: {sources}")
    fm += ["---", "", body]
    (folder / "memo.md").write_text("\n".join(fm))
    if write_pdf:
        (folder / "memo.pdf").write_bytes(pdf_bytes)
    return folder


@pytest.fixture
def memo_dir(tmp_path: Path):
    """Tmp dir + override the module-level repo singleton so routes see our content."""
    content = tmp_path / "memos"
    content.mkdir()
    yield content
    # Reset the singleton so other tests don't see our data.
    research.set_repository(None)  # type: ignore[arg-type]


def _install_repo(memo_dir: Path) -> MemoRepository:
    repo = MemoRepository(memo_dir)
    research.set_repository(repo)
    return repo


@pytest.mark.asyncio
async def test_index_renders_with_no_memos(client, memo_dir):
    _install_repo(memo_dir)
    resp = await client.get("/research")
    assert resp.status_code == 200
    assert b"The " in resp.content  # masthead
    assert b"Curunir" in resp.content
    assert b"First memo on its way" in resp.content  # empty-state copy


@pytest.mark.asyncio
async def test_index_lists_top_three_in_top_fold(client, memo_dir):
    _write_memo(memo_dir, "a", iso_date="2026-05-24", title="Alpha Memo")
    _write_memo(memo_dir, "b", iso_date="2026-05-16", title="Beta Memo")
    _write_memo(memo_dir, "c", iso_date="2026-05-04", title="Gamma Memo")
    _install_repo(memo_dir)

    resp = await client.get("/research")
    assert resp.status_code == 200
    body = resp.text
    # Order matters: newest first
    assert body.index("Alpha Memo") < body.index("Beta Memo") < body.index("Gamma Memo")
    # No archive table when only 3 memos
    assert "Earlier memos" not in body


@pytest.mark.asyncio
async def test_index_archive_appears_with_more_than_three(client, memo_dir):
    for i, slug in enumerate(["a", "b", "c", "d", "e"]):
        _write_memo(memo_dir, slug, iso_date=f"2026-05-0{9 - i}", title=f"Memo {slug}")
    _install_repo(memo_dir)

    resp = await client.get("/research")
    assert resp.status_code == 200
    body = resp.text
    assert "Earlier memos" in body
    # The 4th and 5th memos should appear in the archive table
    assert "Memo d" in body
    assert "Memo e" in body


@pytest.mark.asyncio
async def test_index_tabs_only_show_used_categories(client, memo_dir):
    _write_memo(memo_dir, "a", category="Equities", category_slug="equities")
    _write_memo(memo_dir, "b", category="Commodities", category_slug="commodities")
    _install_repo(memo_dir)

    resp = await client.get("/research")
    body = resp.text
    assert "EQUITIES" in body
    assert "COMMODITIES" in body
    # No tab for a category we didn't use
    assert "MACRO" not in body
    assert "OPTIONS" not in body


@pytest.mark.asyncio
async def test_memo_detail_renders(client, memo_dir):
    _write_memo(
        memo_dir,
        "spacex-ipo-2026-05-24",
        title="SpaceX IPO — Bull vs Bear",
        dek="A working note on the SpaceX listing.",
        body="## Thesis\n\n- Point one.\n- Point two.",
    )
    _install_repo(memo_dir)

    resp = await client.get("/research/spacex-ipo-2026-05-24")
    assert resp.status_code == 200
    body = resp.text
    assert "SpaceX IPO — Bull vs Bear" in body
    assert "A working note on the SpaceX listing." in body
    assert "<h2>Thesis</h2>" in body  # body rendered from markdown
    # OG tags populated from the memo
    assert 'property="og:title" content="SpaceX IPO — Bull vs Bear"' in body
    assert 'property="og:type" content="article"' in body


@pytest.mark.asyncio
async def test_memo_detail_404_for_unknown_slug(client, memo_dir):
    _install_repo(memo_dir)
    resp = await client.get("/research/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_memo_detail_has_share_on_x_link(client, memo_dir):
    _write_memo(memo_dir, "a", title="Memo A", dek="Short dek.")
    _install_repo(memo_dir)

    resp = await client.get("/research/a")
    assert resp.status_code == 200
    # Tweet text should include the canonical URL
    expected = quote("Memo A — Short dek. http://test/research/a", safe="")
    # urlencode applies + for spaces in form-encoding; Jinja's urlencode uses %20
    # so just check the tweet intent URL exists and contains the title
    assert "x.com/intent/tweet?text=" in resp.text
    assert "Memo%20A" in resp.text or "Memo+A" in resp.text


@pytest.mark.asyncio
async def test_memo_pdf_serves(client, memo_dir):
    _write_memo(memo_dir, "a", pdf_bytes=b"%PDF-1.4\nrealish\n")
    _install_repo(memo_dir)

    resp = await client.get("/research/a/memo.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content == b"%PDF-1.4\nrealish\n"


@pytest.mark.asyncio
async def test_memo_pdf_404_when_pdf_missing(client, memo_dir):
    _write_memo(memo_dir, "no-pdf", write_pdf=False)
    _install_repo(memo_dir)

    resp = await client.get("/research/no-pdf/memo.pdf")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_methodology_renders(client, memo_dir):
    _install_repo(memo_dir)
    resp = await client.get("/research/methodology")
    assert resp.status_code == 200
    body = resp.text
    assert "How these memos" in body
    assert "Catalyst" in body
    # Outbound CTA to curunir.ai
    assert "https://curunir.ai" in body


@pytest.mark.asyncio
async def test_methodology_slug_is_reserved(client, memo_dir):
    """Even if someone creates a memo folder named `methodology`,
    the methodology page wins."""
    _write_memo(memo_dir, "methodology", title="A Trick Memo")
    _install_repo(memo_dir)

    resp = await client.get("/research/methodology")
    assert resp.status_code == 200
    # The methodology page renders, NOT the memo
    assert "A Trick Memo" not in resp.text
    assert "How these memos" in resp.text


@pytest.mark.asyncio
async def test_memo_detail_with_missing_pdf_disables_actions(client, memo_dir):
    _write_memo(memo_dir, "no-pdf", write_pdf=False)
    _install_repo(memo_dir)

    resp = await client.get("/research/no-pdf")
    assert resp.status_code == 200
    body = resp.text
    # The PDF embed iframe should not render; the actions should show disabled.
    assert "PDF unavailable" in body
    assert 'src="/research/no-pdf/memo.pdf"' not in body
