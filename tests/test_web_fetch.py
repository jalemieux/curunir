import io
from unittest.mock import MagicMock, patch

import httpx
import pytest
from pypdf import PdfWriter

from src.tools.web_fetch import exec_web_fetch


def _make_response(content=b"", text=None, content_type="text/html", status_code=200):
    resp = MagicMock(spec=httpx.Response)
    resp.content = content
    resp.text = text if text is not None else content.decode("utf-8", errors="replace")
    resp.headers = {"content-type": content_type}
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    return resp


def _build_pdf_bytes(pages_text):
    """Build a real PDF in-memory with the given list of page texts."""
    writer = PdfWriter()
    for txt in pages_text:
        # PdfWriter requires at least a blank page; we use add_blank_page and
        # rely on monkeypatching extract_text in tests where text matters.
        writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestHtmlPath:
    def test_html_returns_extracted_text(self, agent_config):
        html = b"<html><body><article><h1>Title</h1><p>Hello world body content here.</p></article></body></html>"
        resp = _make_response(content=html, content_type="text/html")
        with patch("src.tools.web_fetch.httpx.get", return_value=resp):
            result = exec_web_fetch({"url": "https://example.com/page"}, agent_config)
        assert "Hello world" in result or "Title" in result

    def test_html_trafilatura_returns_none(self, agent_config):
        resp = _make_response(content=b"<html></html>", content_type="text/html")
        with patch("src.tools.web_fetch.httpx.get", return_value=resp), \
             patch("src.tools.web_fetch.extract", return_value=None):
            result = exec_web_fetch({"url": "https://example.com/empty"}, agent_config)
        assert "Could not extract readable content" in result
        assert "https://example.com/empty" in result


class TestPdfPath:
    def test_pdf_content_type_extracts_text(self, agent_config):
        pdf_bytes = _build_pdf_bytes(["page one"])
        resp = _make_response(content=pdf_bytes, content_type="application/pdf")
        with patch("src.tools.web_fetch.httpx.get", return_value=resp), \
             patch("src.tools.web_fetch.extract") as mock_trafilatura, \
             patch("pypdf.PdfReader") as mock_reader_cls:
            mock_page = MagicMock()
            mock_page.extract_text.return_value = "Extracted PDF page text"
            mock_reader = MagicMock()
            mock_reader.is_encrypted = False
            mock_reader.pages = [mock_page]
            mock_reader_cls.return_value = mock_reader

            result = exec_web_fetch({"url": "https://example.com/doc.pdf"}, agent_config)

        assert "Extracted PDF page text" in result
        mock_trafilatura.assert_not_called()

    def test_pdf_magic_bytes_fallback(self, agent_config):
        pdf_bytes = b"%PDF-1.4\n" + b"x" * 100
        resp = _make_response(content=pdf_bytes, content_type="text/html")
        with patch("src.tools.web_fetch.httpx.get", return_value=resp), \
             patch("src.tools.web_fetch.extract") as mock_trafilatura, \
             patch("pypdf.PdfReader") as mock_reader_cls:
            mock_page = MagicMock()
            mock_page.extract_text.return_value = "From magic-byte PDF"
            mock_reader = MagicMock()
            mock_reader.is_encrypted = False
            mock_reader.pages = [mock_page]
            mock_reader_cls.return_value = mock_reader

            result = exec_web_fetch({"url": "https://example.com/mystery"}, agent_config)

        assert "From magic-byte PDF" in result
        mock_trafilatura.assert_not_called()

    def test_pdf_octet_stream_with_magic_bytes(self, agent_config):
        pdf_bytes = b"%PDF-1.4\n" + b"x" * 100
        resp = _make_response(content=pdf_bytes, content_type="application/octet-stream")
        with patch("src.tools.web_fetch.httpx.get", return_value=resp), \
             patch("pypdf.PdfReader") as mock_reader_cls:
            mock_page = MagicMock()
            mock_page.extract_text.return_value = "octet-stream pdf"
            mock_reader = MagicMock()
            mock_reader.is_encrypted = False
            mock_reader.pages = [mock_page]
            mock_reader_cls.return_value = mock_reader

            result = exec_web_fetch({"url": "https://example.com/binary"}, agent_config)

        assert "octet-stream pdf" in result

    def test_encrypted_pdf(self, agent_config):
        pdf_bytes = b"%PDF-1.4\n"
        resp = _make_response(content=pdf_bytes, content_type="application/pdf")
        with patch("src.tools.web_fetch.httpx.get", return_value=resp), \
             patch("pypdf.PdfReader") as mock_reader_cls:
            mock_reader = MagicMock()
            mock_reader.is_encrypted = True
            mock_reader_cls.return_value = mock_reader

            result = exec_web_fetch({"url": "https://example.com/locked.pdf"}, agent_config)

        assert "encrypted PDF" in result
        assert "https://example.com/locked.pdf" in result

    def test_scanned_pdf_empty_text(self, agent_config):
        pdf_bytes = b"%PDF-1.4\n"
        resp = _make_response(content=pdf_bytes, content_type="application/pdf")
        with patch("src.tools.web_fetch.httpx.get", return_value=resp), \
             patch("pypdf.PdfReader") as mock_reader_cls:
            mock_page = MagicMock()
            mock_page.extract_text.return_value = "   "
            mock_reader = MagicMock()
            mock_reader.is_encrypted = False
            mock_reader.pages = [mock_page]
            mock_reader_cls.return_value = mock_reader

            result = exec_web_fetch({"url": "https://example.com/scan.pdf"}, agent_config)

        assert "scanned" in result.lower() or "image-based" in result.lower()
        assert "https://example.com/scan.pdf" in result

    def test_malformed_pdf(self, agent_config):
        pdf_bytes = b"%PDF-1.4\nbroken garbage"
        resp = _make_response(content=pdf_bytes, content_type="application/pdf")
        from pypdf.errors import PdfReadError
        with patch("src.tools.web_fetch.httpx.get", return_value=resp), \
             patch("pypdf.PdfReader", side_effect=PdfReadError("bad pdf")):
            result = exec_web_fetch({"url": "https://example.com/bad.pdf"}, agent_config)

        assert "Error parsing PDF" in result
        assert "https://example.com/bad.pdf" in result

    def test_pdf_truncation(self, agent_config):
        from src.tools import web_fetch
        pdf_bytes = b"%PDF-1.4\n"
        resp = _make_response(content=pdf_bytes, content_type="application/pdf")
        big_text = "A" * (web_fetch._MAX_CONTENT_CHARS + 10_000)
        with patch("src.tools.web_fetch.httpx.get", return_value=resp), \
             patch("pypdf.PdfReader") as mock_reader_cls:
            mock_page = MagicMock()
            mock_page.extract_text.return_value = big_text
            mock_reader = MagicMock()
            mock_reader.is_encrypted = False
            mock_reader.pages = [mock_page]
            mock_reader_cls.return_value = mock_reader

            result = exec_web_fetch({"url": "https://example.com/huge.pdf"}, agent_config)

        assert "truncated" in result
        assert len(result) < len(big_text)


class TestErrors:
    def test_missing_url(self, agent_config):
        result = exec_web_fetch({}, agent_config)
        assert "url" in result.lower()

    def test_http_error(self, agent_config):
        with patch("src.tools.web_fetch.httpx.get",
                   side_effect=httpx.HTTPError("boom")):
            result = exec_web_fetch({"url": "https://example.com/bad"}, agent_config)
        assert "Error fetching" in result
        assert "https://example.com/bad" in result
