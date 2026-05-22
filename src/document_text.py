"""Text extraction for document attachments (PDF, DOCX).

``build_multimodal_content`` (run.py) turns inbound attachments into LLM
content blocks. PDF and DOCX parsing can raise — pypdf in particular is
stricter than a typical PDF viewer, and needs the optional ``cryptography``
package to read encrypted PDFs — so every failure here resolves to a
descriptive text block rather than an exception that would escape
``agent_worker`` and cancel the TaskGroup that owns every channel.
"""

import logging

logger = logging.getLogger(__name__)


def pdf_to_text_block(filename: str, path: str) -> str:
    """Extract a PDF attachment into a text block, degrading gracefully.

    Any pypdf failure (encryption, unsupported filters, parse errors) resolves
    to a descriptive block; a single unreadable page is skipped, not fatal.
    """
    from pypdf import PdfReader
    try:
        reader = PdfReader(path)
        pages = list(reader.pages)
    except Exception as e:
        logger.warning("PDF attachment %s could not be parsed: %s", filename, e)
        return f"[Attachment: {filename} (PDF) — could not be parsed: {e}]"

    extracted: list[str] = []
    for page in pages:
        try:
            extracted.append(page.extract_text() or "")
        except Exception as e:
            logger.warning("PDF attachment %s: page extract failed: %s", filename, e)
    body = "\n\n".join(p for p in extracted if p).strip() or "(no extractable text)"
    return f"[Attachment: {filename} (PDF, {len(pages)} pages)]\n```\n{body}\n```"


def docx_to_text_block(filename: str, path: str) -> str:
    """Extract a DOCX attachment into a text block, degrading gracefully.

    A DOCX python-docx cannot open resolves to a descriptive block rather than
    raising an exception that would escape ``agent_worker``.
    """
    try:
        import docx
        doc = docx.Document(path)
        body = "\n".join(p.text for p in doc.paragraphs).strip() or "(no extractable text)"
    except Exception as e:
        logger.warning("DOCX attachment %s could not be parsed: %s", filename, e)
        return f"[Attachment: {filename} (DOCX) — could not be parsed: {e}]"
    return f"[Attachment: {filename} (DOCX)]\n```\n{body}\n```"
