"""Tests for run.build_multimodal_content."""
import base64
import os

import pytest


@pytest.fixture
def text_file(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("hello world")
    return p


@pytest.fixture
def image_file(tmp_path):
    p = tmp_path / "img.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    return p


def _att(path, mime):
    return {"filename": os.path.basename(path), "path": str(path),
            "mime_type": mime, "size": os.path.getsize(path)}


def test_no_attachments_returns_string():
    from run import build_multimodal_content
    assert build_multimodal_content("hi", None) == "hi"
    assert build_multimodal_content("hi", []) == "hi"


def test_single_image_produces_text_and_image_block(image_file):
    from run import build_multimodal_content
    blocks = build_multimodal_content("describe", [_att(image_file, "image/png")])
    assert isinstance(blocks, list)
    assert len(blocks) == 2
    assert blocks[0] == {"type": "text", "text": "describe"}
    assert blocks[1]["type"] == "image_url"
    url = blocks[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    b64 = url.split(",", 1)[1]
    with open(image_file, "rb") as f:
        assert base64.b64decode(b64) == f.read()


def test_single_text_file_becomes_text_block(text_file):
    from run import build_multimodal_content
    blocks = build_multimodal_content("compare", [_att(text_file, "text/plain")])
    assert isinstance(blocks, list)
    assert blocks[0] == {"type": "text", "text": "compare"}
    assert blocks[1]["type"] == "text"
    assert "notes.txt" in blocks[1]["text"]
    assert "hello world" in blocks[1]["text"]


def test_empty_prompt_with_image_skips_leading_text_block(image_file):
    from run import build_multimodal_content
    blocks = build_multimodal_content("", [_att(image_file, "image/png")])
    assert len(blocks) == 1
    assert blocks[0]["type"] == "image_url"


def test_mixed_ordering_preserved(image_file, text_file):
    from run import build_multimodal_content
    blocks = build_multimodal_content(
        "look",
        [_att(image_file, "image/png"), _att(text_file, "text/plain")],
    )
    types = [b["type"] for b in blocks]
    assert types == ["text", "image_url", "text"]
    assert blocks[0]["text"] == "look"


import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from src.channels.base import IncomingMessage


@pytest.mark.asyncio
async def test_agent_worker_sends_multimodal_for_cli_channel(tmp_path, monkeypatch, image_file):
    """CLI inbound with attachments calls agent.handle with a list content."""
    import run as run_module
    from src.config import AgentConfig

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()

    captured: dict = {}
    fake_agent = type("A", (), {})()
    fake_agent.config = AgentConfig()

    async def fake_handle(content, session_id, **kwargs):
        captured["content"] = content
        return "ok"

    fake_agent.handle = fake_handle
    fake_agent.sessions = {}

    msg = IncomingMessage(
        content="look",
        channel="cli",
        session_id="cli",
        reply_address={},
        attachments=[_att(image_file, "image/png")],
    )
    await in_q.put(msg)

    task = asyncio.create_task(run_module.agent_worker(fake_agent, in_q, out_q))
    out = await asyncio.wait_for(out_q.get(), timeout=2.0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert out.content == "ok"
    assert isinstance(captured["content"], list)
    assert captured["content"][0]["type"] == "text"
    assert captured["content"][1]["type"] == "image_url"


@pytest.mark.asyncio
async def test_reset_command_purges_uploads_for_cli_session(tmp_path, monkeypatch):
    """`/clear` or `/reset` wipes context/uploads/<session_id>/."""
    import run as run_module
    from src.config import AgentConfig

    monkeypatch.chdir(tmp_path)
    session_dir = Path("context/uploads/cli")
    session_dir.mkdir(parents=True)
    (session_dir / "leftover.txt").write_text("old")

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()

    fake_agent = type("A", (), {})()
    fake_agent.config = AgentConfig()
    fake_agent.sessions = {"cli": []}
    fake_agent.handle = AsyncMock(return_value="")

    await in_q.put(IncomingMessage(
        content="", channel="cli", session_id="cli", reply_address={}, command="clear",
    ))

    task = asyncio.create_task(run_module.agent_worker(fake_agent, in_q, out_q))
    await asyncio.wait_for(out_q.get(), timeout=2.0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert not session_dir.exists(), "uploads/<session_id>/ should be purged on /clear"
