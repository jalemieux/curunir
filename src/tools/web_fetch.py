# src/tools/web_fetch.py
import io
import logging
from urllib.parse import urlsplit

import httpx
import pypdf
from pypdf.errors import PdfReadError
from trafilatura import extract

from src.config import AgentConfig

logger = logging.getLogger(__name__)

_MAX_CONTENT_CHARS = 20_000  # ~5k tokens
_TIMEOUT = 30
_PDF_MAGIC = b"%PDF-"
_MAX_DNS_LABEL = 63  # bytes; the IDNA codec raises "label too long" past this


def _validate_url(url: str) -> str | None:
    """Return an error string if the URL is structurally unfetchable, else None.

    Catches hallucinated/malformed URLs (e.g. a hostname whose dot-separated
    label exceeds 63 bytes) before httpx encodes the host as IDNA and raises a
    bare UnicodeError("label too long").
    """
    try:
        parts = urlsplit(url)
    except ValueError as e:
        return f"Error: {url} is not a valid URL ({e})"

    if parts.scheme not in ("http", "https"):
        return f"Error: {url} is not a valid URL (scheme must be http or https)"

    try:
        host = parts.hostname
    except ValueError as e:
        return f"Error: {url} is not a valid URL ({e})"
    if not host:
        return f"Error: {url} is not a valid URL (missing host)"

    for label in host.split("."):
        if len(label.encode("utf-8")) > _MAX_DNS_LABEL:
            return (
                f"Error: {url} is not a valid URL "
                f"(hostname label exceeds {_MAX_DNS_LABEL} characters)"
            )

    return None


def exec_web_fetch(args: dict, config: AgentConfig) -> str:
    """Fetch a URL and return extracted text content (HTML or PDF)."""
    url = args.get("url", "")
    if not url:
        return "Error: 'url' is required"

    validation_error = _validate_url(url)
    if validation_error:
        return validation_error

    try:
        resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; research-agent/1.0)",
        })
        resp.raise_for_status()
    except (httpx.HTTPError, httpx.InvalidURL, UnicodeError, ValueError) as e:
        return f"Error fetching {url}: {e}"

    content_type = resp.headers.get("content-type", "").lower()
    if "pdf" in content_type or resp.content[:5] == _PDF_MAGIC:
        return _extract_pdf_text(resp.content, url)

    text = extract(resp.text, include_links=True, include_tables=True)
    if not text:
        return f"Could not extract readable content from {url}"

    if len(text) > _MAX_CONTENT_CHARS:
        text = text[:_MAX_CONTENT_CHARS] + f"\n\n... truncated ({len(text)} chars total)"

    return text


def _extract_pdf_text(content: bytes, url: str) -> str:
    """Extract plain text from PDF bytes via pypdf."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
    except PdfReadError as e:
        return f"Error parsing PDF at {url}: {e}"
    except Exception as e:
        return f"Error parsing PDF at {url}: {e}"

    if reader.is_encrypted:
        return f"Error: {url} is an encrypted PDF; cannot extract text."

    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception as e:
            logger.warning("pypdf page extract failed for %s: %s", url, e)

    text = "\n\n".join(p for p in pages if p).strip()
    if not text:
        return (
            f"Could not extract text from PDF at {url} "
            "(likely scanned/image-based; OCR not supported)."
        )

    page_count = len(reader.pages)
    original_len = len(text)
    if original_len > _MAX_CONTENT_CHARS:
        text = text[:_MAX_CONTENT_CHARS] + f"\n\n... truncated ({original_len} chars total)"

    logger.info(
        "web_fetch extracted PDF: url=%s pages=%d chars=%d",
        url, page_count, original_len,
    )
    return text
