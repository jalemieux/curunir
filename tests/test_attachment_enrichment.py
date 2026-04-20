"""Tests for attachment enrichment in agent_worker."""
import os
import tempfile

import pytest

# Import the enrichment function we'll create
from run import _enrich_attachments

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
    task = asyncio.create_task(agent_worker(agent, in_q, out_q))
    result = await asyncio.wait_for(out_q.get(), timeout=2.0)
    task.cancel()

    assert result.workflow == {"steps": ["plan", "build"], "current": "build"}
