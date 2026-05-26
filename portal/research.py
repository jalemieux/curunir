"""Public Research section.

Routes:
- GET /research                       — broadsheet index
- GET /research/methodology           — long-form methodology page (reserved)
- GET /research/{slug}                — per-memo permalink
- GET /research/{slug}/memo.pdf       — serve the PDF for a memo

The methodology route is declared before the slug route so FastAPI
matches the literal path first; `methodology` is also in RESERVED_SLUGS
so a memo folder named `methodology` will never shadow it.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates

from portal.config import settings
from portal.memos import MemoRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Used in footer year stamps across all research templates.
import datetime as _dt
templates.env.globals["now_year"] = _dt.date.today().year

# Methodology lives at /research/methodology, so no memo can claim that slug.
RESERVED_SLUGS = frozenset({"methodology"})

_DEFAULT_CONTENT_DIR = Path(__file__).parent / "content" / "memos"


_repository: MemoRepository | None = None


def get_repository() -> MemoRepository:
    """Module-level singleton. Init lazily so import order doesn't matter."""
    global _repository
    if _repository is None:
        _repository = MemoRepository(_DEFAULT_CONTENT_DIR)
    return _repository


def set_repository(repo: MemoRepository | None) -> None:
    """Used by tests to inject a repo (or reset to None for re-init)."""
    global _repository
    _repository = repo


def _canonical_url(request: Request) -> str:
    base = settings.portal_base_url.rstrip("/")
    return f"{base}{request.url.path}"


@router.get("")
async def research_index(request: Request):
    repo = get_repository()
    memos = repo.list()
    top = memos[:3]
    earlier = memos[3:]
    return templates.TemplateResponse(
        request,
        "research_index.html",
        {
            "memos": memos,
            "top": top,
            "earlier": earlier,
            "categories": repo.categories(),
            "latest_date": memos[0].date if memos else None,
            "canonical_url": _canonical_url(request),
        },
    )


@router.get("/methodology")
async def research_methodology(request: Request):
    return templates.TemplateResponse(
        request,
        "research_methodology.html",
        {
            "canonical_url": _canonical_url(request),
        },
    )


@router.get("/{slug}")
async def memo_detail(request: Request, slug: str):
    if slug in RESERVED_SLUGS:
        # Defensive: the explicit methodology route should have matched first,
        # but if someone wires a new reserved slug they don't get a confusing
        # 404 from the memo lookup path.
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    repo = get_repository()
    memo = repo.get(slug)
    if memo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    # "Earlier memos" = three most recent excluding this one.
    other = [m for m in repo.list() if m.slug != memo.slug][:3]

    return templates.TemplateResponse(
        request,
        "memo_detail.html",
        {
            "memo": memo,
            "other": other,
            "canonical_url": _canonical_url(request),
        },
    )


@router.get("/{slug}/memo.pdf")
async def memo_pdf(slug: str):
    if slug in RESERVED_SLUGS:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    repo = get_repository()
    memo = repo.get(slug)
    if memo is None or not memo.pdf_exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    return FileResponse(
        repo.pdf_path(memo),
        media_type="application/pdf",
        filename=memo.pdf_filename,
    )
