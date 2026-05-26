"""Memo content model for the Research section.

A memo lives on disk as a folder under `portal/content/memos/<slug>/`:

    <slug>/
      memo.md     # YAML frontmatter + markdown summary body
      memo.pdf    # the full report (filename declared in frontmatter)

`MemoRepository` scans that root, parses each `memo.md`, renders the body
to HTML, and exposes a newest-first list plus per-slug lookup. Malformed
or incomplete memos are logged and skipped — one bad memo never takes
down the whole index.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

import frontmatter
from markdown_it import MarkdownIt

logger = logging.getLogger(__name__)


REQUIRED_FIELDS = ("title", "date", "category", "category_slug", "dek", "pdf")


@dataclass(frozen=True)
class Memo:
    slug: str
    title: str
    date: date
    category: str
    category_slug: str
    dek: str
    pdf_filename: str
    body_html: str
    pdf_exists: bool
    pages: int | None = None
    sources: int | None = None


_PULL_RE = re.compile(
    r"<blockquote>\s*<p>\[!pull\]\s*(?:<br\s*/?>)?\s*(.*?)</p>\s*</blockquote>",
    re.DOTALL,
)


def _render_markdown(body: str) -> str:
    """Render markdown to HTML, with our custom pull-quote rule.

    A blockquote whose first line is `[!pull]` becomes
    `<blockquote class="pull">…</blockquote>`, with the marker stripped.
    """
    md = MarkdownIt("commonmark")
    html = md.render(body)
    return _PULL_RE.sub(r'<blockquote class="pull"><p>\1</p></blockquote>', html)


def _parse_memo_folder(folder: Path) -> Memo | None:
    """Parse one memo folder. Returns None and logs on any failure."""
    memo_md = folder / "memo.md"
    if not memo_md.is_file():
        return None

    try:
        post = frontmatter.load(memo_md)
    except Exception as exc:
        logger.warning("memo %s: frontmatter parse failed: %s", folder.name, exc)
        return None

    missing = [f for f in REQUIRED_FIELDS if f not in post.metadata]
    if missing:
        logger.warning(
            "memo %s: missing required frontmatter %s — skipping",
            folder.name,
            missing,
        )
        return None

    raw_date = post["date"]
    if isinstance(raw_date, str):
        try:
            parsed_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            logger.warning("memo %s: bad date %r: %s", folder.name, raw_date, exc)
            return None
    elif isinstance(raw_date, date):
        parsed_date = raw_date
    else:
        logger.warning("memo %s: date is not str/date (got %r)", folder.name, raw_date)
        return None

    pdf_filename = str(post["pdf"])
    pdf_path = folder / pdf_filename
    pdf_exists = pdf_path.is_file()
    if not pdf_exists:
        logger.warning("memo %s: pdf %s not found on disk", folder.name, pdf_filename)

    return Memo(
        slug=folder.name,
        title=str(post["title"]),
        date=parsed_date,
        category=str(post["category"]),
        category_slug=str(post["category_slug"]),
        dek=str(post["dek"]),
        pdf_filename=pdf_filename,
        body_html=_render_markdown(post.content),
        pdf_exists=pdf_exists,
        pages=post.metadata.get("pages"),
        sources=post.metadata.get("sources"),
    )


class MemoRepository:
    """In-memory cache of parsed memos.

    Reads the content directory once on construction (and on `reload()`).
    Safe to call against a directory that doesn't exist — produces an
    empty repo.
    """

    def __init__(self, content_dir: Path):
        self._content_dir = content_dir
        self._memos: list[Memo] = []
        self.reload()

    def reload(self) -> None:
        if not self._content_dir.is_dir():
            self._memos = []
            return
        memos: list[Memo] = []
        for entry in sorted(self._content_dir.iterdir()):
            if not entry.is_dir():
                continue
            memo = _parse_memo_folder(entry)
            if memo is not None:
                memos.append(memo)
        memos.sort(key=lambda m: m.date, reverse=True)
        self._memos = memos

    def list(self) -> list[Memo]:
        return list(self._memos)

    def get(self, slug: str) -> Memo | None:
        for memo in self._memos:
            if memo.slug == slug:
                return memo
        return None

    def categories(self) -> list[tuple[str, str]]:
        """Return (slug, label) for categories that have at least one memo, sorted by label."""
        seen: dict[str, str] = {}
        for memo in self._memos:
            seen.setdefault(memo.category_slug, memo.category)
        return sorted(seen.items(), key=lambda kv: kv[1])

    def pdf_path(self, memo: Memo) -> Path:
        """Absolute path to a memo's PDF on disk (may not exist)."""
        return self._content_dir / memo.slug / memo.pdf_filename
