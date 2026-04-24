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
