"""Tests for attachment enrichment in agent_worker."""
import os
import tempfile

import pytest

from src.channels.ws import _enrich_attachments

_MAX_CONTENT_SIZE = 512 * 1024  # 512KB


def test_enrich_reads_text_content():
    """Text-based attachments get their content read into the dict."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, dir="/tmp") as f:
        f.write("# Hello\n\nWorld")
        path = f.name

    try:
        attachments = [{"filename": "hello.md", "path": path, "mime_type": "text/markdown", "size": 15}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert attachments[0]["content"] == "# Hello\n\nWorld"
        assert attachments[0]["path"] == os.path.basename(path)  # relative
    finally:
        os.unlink(path)


def test_enrich_reads_json_content():
    """application/json attachments get content included."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, dir="/tmp") as f:
        f.write('{"key": "value"}')
        path = f.name

    try:
        attachments = [{"filename": "data.json", "path": path, "mime_type": "application/json", "size": 16}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert attachments[0]["content"] == '{"key": "value"}'
    finally:
        os.unlink(path)


def test_enrich_skips_binary_content():
    """Binary attachments (e.g. image/png) get content: null."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir="/tmp") as f:
        f.write(b"\x89PNG\r\n")
        path = f.name

    try:
        attachments = [{"filename": "image.png", "path": path, "mime_type": "image/png", "size": 6}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert attachments[0]["content"] is None
    finally:
        os.unlink(path)


def test_enrich_caps_large_files():
    """Files larger than 512KB get content: null."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, dir="/tmp") as f:
        f.write("x" * (_MAX_CONTENT_SIZE + 1))
        path = f.name

    try:
        attachments = [{"filename": "big.md", "path": path, "mime_type": "text/markdown", "size": _MAX_CONTENT_SIZE + 1}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert attachments[0]["content"] is None
    finally:
        os.unlink(path)


def test_enrich_handles_missing_file():
    """Deleted files get content: null and an error field."""
    attachments = [{"filename": "gone.md", "path": "/tmp/nonexistent-file-xyz.md", "mime_type": "text/markdown", "size": 100}]
    _enrich_attachments(attachments, project_root="/tmp")
    assert attachments[0]["content"] is None
    assert attachments[0]["error"] == "file not found"


def test_enrich_normalizes_path():
    """Absolute paths are normalized to be relative to project root."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, dir="/tmp") as f:
        f.write("content")
        path = f.name

    try:
        attachments = [{"filename": "test.md", "path": path, "mime_type": "text/markdown", "size": 7}]
        _enrich_attachments(attachments, project_root="/tmp")
        # Path should be relative — just the filename
        assert not attachments[0]["path"].startswith("/")
    finally:
        os.unlink(path)


import base64 as _b64


def test_enrich_adds_data_for_image_under_cap():
    """Images under 5 MB get base64 `data` for download."""
    payload = b"\x89PNG\r\n" + b"\x00" * 1024
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir="/tmp") as f:
        f.write(payload)
        path = f.name
    try:
        attachments = [{"filename": "img.png", "path": path,
                        "mime_type": "image/png", "size": len(payload)}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert attachments[0]["data"] == _b64.b64encode(payload).decode("ascii")
    finally:
        os.unlink(path)


def test_enrich_adds_data_for_pdf_under_cap():
    """PDFs under 10 MB get base64 `data` for download."""
    payload = b"%PDF-1.4\n" + b"\x00" * 2048
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir="/tmp") as f:
        f.write(payload)
        path = f.name
    try:
        attachments = [{"filename": "doc.pdf", "path": path,
                        "mime_type": "application/pdf", "size": len(payload)}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert attachments[0]["data"] == _b64.b64encode(payload).decode("ascii")
    finally:
        os.unlink(path)


def test_enrich_adds_data_for_text_under_cap():
    """Text files ≤ 256 KB get both `content` (string) and `data` (base64)."""
    body = "hello world"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, dir="/tmp") as f:
        f.write(body)
        path = f.name
    try:
        attachments = [{"filename": "hello.md", "path": path,
                        "mime_type": "text/markdown", "size": len(body)}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert attachments[0]["content"] == body
        assert attachments[0]["data"] == _b64.b64encode(body.encode("utf-8")).decode("ascii")
    finally:
        os.unlink(path)


def test_enrich_omits_data_for_image_over_cap():
    """Images > 5 MB get no `data` field."""
    size = 5 * 1024 * 1024 + 1
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir="/tmp") as f:
        f.write(b"\x00" * size)
        path = f.name
    try:
        attachments = [{"filename": "big.png", "path": path,
                        "mime_type": "image/png", "size": size}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert "data" not in attachments[0]
    finally:
        os.unlink(path)


def test_enrich_omits_data_for_pdf_over_cap():
    """PDFs > 10 MB get no `data` field."""
    size = 10 * 1024 * 1024 + 1
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir="/tmp") as f:
        f.write(b"\x00" * size)
        path = f.name
    try:
        attachments = [{"filename": "big.pdf", "path": path,
                        "mime_type": "application/pdf", "size": size}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert "data" not in attachments[0]
    finally:
        os.unlink(path)


def test_enrich_omits_data_for_text_over_data_cap():
    """Text files > 256 KB get no `data` (even if under the 512 KB content cap)."""
    size = 256 * 1024 + 1
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, dir="/tmp") as f:
        f.write("x" * size)
        path = f.name
    try:
        attachments = [{"filename": "big.md", "path": path,
                        "mime_type": "text/markdown", "size": size}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert "data" not in attachments[0]
        # `content` is still populated since 256 KB+1 < 512 KB content cap.
        assert attachments[0]["content"] is not None
    finally:
        os.unlink(path)


def test_enrich_omits_data_for_unsupported_mime():
    """Mimes outside the allowed image/doc/text sets get no `data`."""
    payload = b"PK\x03\x04fakezip"
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False, dir="/tmp") as f:
        f.write(payload)
        path = f.name
    try:
        attachments = [{"filename": "thing.zip", "path": path,
                        "mime_type": "application/zip", "size": len(payload)}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert "data" not in attachments[0]
    finally:
        os.unlink(path)


def test_enrich_missing_file_sets_error_no_data():
    """Missing file: `error` set, no `data` field, even for downloadable mime."""
    attachments = [{"filename": "gone.pdf", "path": "/tmp/nonexistent-xyz.pdf",
                    "mime_type": "application/pdf", "size": 100}]
    _enrich_attachments(attachments, project_root="/tmp")
    assert "data" not in attachments[0]
    assert attachments[0]["error"] == "file not found"


def test_enrich_is_idempotent_for_data():
    """Calling `_enrich_attachments` twice produces the same `data`."""
    payload = b"hello"
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir="/tmp") as f:
        f.write(payload)
        path = f.name
    try:
        attachments = [{"filename": "x.png", "path": path,
                        "mime_type": "image/png", "size": len(payload)}]
        _enrich_attachments(attachments, project_root="/tmp")
        first = attachments[0]["data"]
        # Second call: path is now relative; _enrich must still find the file.
        # Use absolute path again to simulate a fresh enrichment cycle.
        attachments[0]["path"] = path
        _enrich_attachments(attachments, project_root="/tmp")
        assert attachments[0]["data"] == first
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_agent_worker_passes_workflow_to_outgoing():
    """Workflow metadata from agent.handle() propagates to OutgoingMessage."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from src.channels.base import IncomingMessage

    # Mock agent whose handle() populates metadata with workflow
    agent = MagicMock()
    agent.sessions = {}

    async def fake_handle(content, session_id, **kwargs):
        metadata = kwargs.get("metadata")
        if metadata is not None:
            metadata["workflow"] = {"steps": ["plan", "build"], "current": "build"}
        return "done"

    agent.handle = AsyncMock(side_effect=fake_handle)

    in_q = asyncio.Queue()
    out_q = asyncio.Queue()

    msg = IncomingMessage(content="go", channel="cli", session_id="test", reply_address={})
    await in_q.put(msg)

    # Import and run one iteration of agent_worker
    from run import agent_worker
    from src.runtime import AgentRuntime
    registry = {"default": AgentRuntime("default", agent.config, agent)}
    task = asyncio.create_task(agent_worker(registry, in_q, out_q))
    result = await asyncio.wait_for(out_q.get(), timeout=2.0)
    task.cancel()

    assert result.workflow == {"steps": ["plan", "build"], "current": "build"}
