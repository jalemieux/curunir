"""Tests for run.build_multimodal_content."""
import asyncio
import base64
import os
from unittest.mock import AsyncMock, patch

import pytest

from src.channels.base import IncomingMessage


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


def test_empty_prompt_with_image_uses_placeholder_text(image_file):
    """Anthropic rejects empty text blocks, so we seed a placeholder referencing
    the attachment filenames when the user provides no message."""
    from run import build_multimodal_content
    blocks = build_multimodal_content("", [_att(image_file, "image/png")])
    assert len(blocks) == 2
    assert blocks[0]["type"] == "text"
    assert blocks[0]["text"]  # non-empty
    assert "img.png" in blocks[0]["text"]
    assert blocks[1]["type"] == "image_url"


def test_mixed_ordering_preserved(image_file, text_file):
    from run import build_multimodal_content
    blocks = build_multimodal_content(
        "look",
        [_att(image_file, "image/png"), _att(text_file, "text/plain")],
    )
    types = [b["type"] for b in blocks]
    assert types == ["text", "image_url", "text"]
    assert blocks[0]["text"] == "look"


def test_pdf_attachment_produces_text_block_with_extracted_content(tmp_path, monkeypatch):
    """PDF attachments get extracted to text and wrapped with a filename header."""
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")  # contents irrelevant; PdfReader is patched

    class FakePage:
        def extract_text(self):
            return "Hello from PDF"

    class FakeReader:
        def __init__(self, path):
            self.pages = [FakePage(), FakePage()]

    import pypdf
    monkeypatch.setattr(pypdf, "PdfReader", FakeReader)

    from run import build_multimodal_content
    blocks = build_multimodal_content("summarize", [_att(pdf_path, "application/pdf")])

    assert isinstance(blocks, list)
    assert len(blocks) == 2
    assert blocks[0] == {"type": "text", "text": "summarize"}
    assert blocks[1]["type"] == "text"
    body = blocks[1]["text"]
    assert "report.pdf" in body
    assert "(PDF, 2 pages)" in body
    assert "Hello from PDF" in body


def test_docx_attachment_produces_text_block_with_extracted_content(tmp_path, monkeypatch):
    """DOCX attachments get extracted via python-docx and wrapped with a filename header."""
    docx_path = tmp_path / "memo.docx"
    docx_path.write_bytes(b"PK\x03\x04")  # zip magic; contents irrelevant — Document is patched

    class FakePara:
        def __init__(self, text):
            self.text = text

    class FakeDoc:
        def __init__(self, path):
            self.paragraphs = [FakePara("Hello from DOCX"), FakePara("Second line")]

    import sys, types
    fake = types.ModuleType("docx")
    fake.Document = FakeDoc
    monkeypatch.setitem(sys.modules, "docx", fake)

    from run import build_multimodal_content
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    blocks = build_multimodal_content("summarize", [_att(docx_path, mime)])

    assert isinstance(blocks, list)
    assert len(blocks) == 2
    assert blocks[0] == {"type": "text", "text": "summarize"}
    assert blocks[1]["type"] == "text"
    body = blocks[1]["text"]
    assert "memo.docx" in body
    assert "(DOCX)" in body
    assert "Hello from DOCX" in body
    assert "Second line" in body


def test_unknown_binary_attachment_falls_back_to_notice(tmp_path):
    """Binary files that aren't decodable as UTF-8 must not crash — emit a notice instead."""
    bin_path = tmp_path / "blob.bin"
    bin_path.write_bytes(b"\xe4\xa0\xff\x00\x01garbage\x80")

    from run import build_multimodal_content
    blocks = build_multimodal_content(
        "what is this", [_att(bin_path, "application/octet-stream")]
    )

    assert isinstance(blocks, list)
    assert len(blocks) == 2
    assert blocks[1]["type"] == "text"
    notice = blocks[1]["text"]
    assert "blob.bin" in notice
    assert "binary" in notice.lower()
    assert "application/octet-stream" in notice


async def _run_worker_once(in_q, out_q, fake_agent):
    """Drive agent_worker for exactly one message, then cancel cleanly."""
    import run as run_module
    task = asyncio.create_task(run_module.agent_worker(fake_agent, in_q, out_q))
    await asyncio.wait_for(out_q.get(), timeout=2.0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _fake_agent(captured: dict, *, supports_vision: bool = True,
                vision_model: str | None = None):
    from src.config import AgentConfig
    agent = type("A", (), {})()
    agent.config = AgentConfig(
        main_model_supports_vision=supports_vision,
        vision_model=vision_model,
    )
    agent.sessions = {}

    async def fake_handle(content, session_id, **kwargs):
        captured["content"] = content
        return "ok"

    agent.handle = fake_handle
    return agent


@pytest.mark.asyncio
async def test_agent_worker_sends_multimodal_for_cli_channel(image_file):
    """CLI inbound with attachments calls agent.handle with a list content."""
    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    captured: dict = {}

    msg = IncomingMessage(
        content="look", channel="cli", session_id="cli", reply_address={},
        attachments=[_att(image_file, "image/png")],
    )
    await in_q.put(msg)
    await _run_worker_once(in_q, out_q, _fake_agent(captured))

    assert isinstance(captured["content"], list)
    assert captured["content"][0]["type"] == "text"
    assert captured["content"][1]["type"] == "image_url"


@pytest.mark.asyncio
async def test_agent_worker_empty_prompt_with_attachment_seeds_placeholder(image_file):
    """Empty user prompt with an attachment gets a filename-referencing placeholder."""
    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    captured: dict = {}

    msg = IncomingMessage(
        content="", channel="cli", session_id="cli", reply_address={},
        attachments=[_att(image_file, "image/png")],
    )
    await in_q.put(msg)
    await _run_worker_once(in_q, out_q, _fake_agent(captured))

    content = captured["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[0]["text"]  # non-empty, satisfies Anthropic's no-empty-block rule
    assert "img.png" in content[0]["text"]
    assert content[1]["type"] == "image_url"


@pytest.mark.asyncio
async def test_agent_worker_non_cli_channel_builds_multimodal(image_file):
    """Non-CLI channels (e.g. email) with attachments also go through build_multimodal_content."""
    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    captured: dict = {}

    msg = IncomingMessage(
        content="please analyze", channel="email", session_id="thread-123",
        reply_address={"to": "user@example.com"},
        attachments=[_att(image_file, "image/png")],
    )
    await in_q.put(msg)
    await _run_worker_once(in_q, out_q, _fake_agent(captured))

    content = captured["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "please analyze"}
    assert content[1]["type"] == "image_url"


@pytest.mark.asyncio
async def test_text_only_main_with_vision_model_replaces_image_with_description(image_file):
    """Text-only main + configured vision model: describe_image is called and
    its output replaces the image attachment before build_multimodal_content."""
    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    captured: dict = {}

    msg = IncomingMessage(
        content="what is in this picture?", channel="cli",
        session_id="cli", reply_address={},
        attachments=[_att(image_file, "image/png")],
    )
    await in_q.put(msg)

    fake = _fake_agent(captured, supports_vision=False, vision_model="vision/m")
    with patch("run.describe_image", new_callable=AsyncMock) as mock_desc:
        mock_desc.return_value = "A small PNG with a diagonal red line."
        await _run_worker_once(in_q, out_q, fake)

    mock_desc.assert_awaited_once()
    args, kwargs = mock_desc.await_args
    # Signature: describe_image(model, image_path, mime_type, user_prompt, ...)
    assert args[0] == "vision/m"
    assert args[1] == str(image_file)
    assert args[2] == "image/png"
    assert args[3] == "what is in this picture?"

    content = captured["content"]
    assert isinstance(content, list)
    # No image_url block — image was converted to text
    assert all(b["type"] != "image_url" for b in content)
    # The description appears in a text block, prefixed with the filename
    described = next(b for b in content if "img.png" in b.get("text", ""))
    assert "A small PNG with a diagonal red line." in described["text"]


@pytest.mark.asyncio
async def test_text_only_main_no_vision_model_boot_raises(monkeypatch):
    """At boot, if the main model lacks vision and no VISION_MODEL is set,
    main() raises rather than silently degrading images to text markers."""
    import run as run_module

    monkeypatch.setattr(run_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(run_module, "_detect_vision_support", lambda m: False)
    monkeypatch.setenv("MODEL", "text-only/model")
    monkeypatch.delenv("VISION_MODEL", raising=False)

    with pytest.raises(RuntimeError, match="VISION_MODEL"):
        await run_module.main()


@pytest.mark.asyncio
async def test_text_only_main_pdf_and_text_pass_through_unchanged(tmp_path, image_file, monkeypatch):
    """PDFs and plain text attachments are not affected by the vision pre-pass."""
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    txt_path = tmp_path / "notes.txt"
    txt_path.write_text("hello")

    class FakePage:
        def extract_text(self):
            return "PDF body"

    class FakeReader:
        def __init__(self, path):
            self.pages = [FakePage()]

    import pypdf
    monkeypatch.setattr(pypdf, "PdfReader", FakeReader)

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    captured: dict = {}

    msg = IncomingMessage(
        content="review", channel="cli", session_id="cli", reply_address={},
        attachments=[
            _att(pdf_path, "application/pdf"),
            _att(txt_path, "text/plain"),
            _att(image_file, "image/png"),
        ],
    )
    await in_q.put(msg)

    fake = _fake_agent(captured, supports_vision=False, vision_model="vision/m")
    with patch("run.describe_image", new_callable=AsyncMock) as mock_desc:
        mock_desc.return_value = "image description"
        await _run_worker_once(in_q, out_q, fake)

    # Only the image goes through describe_image; pdf and text are untouched
    assert mock_desc.await_count == 1

    content = captured["content"]
    assert isinstance(content, list)
    # PDF block still present and contains extracted text
    assert any("doc.pdf" in b.get("text", "") and "PDF body" in b.get("text", "")
               for b in content)
    # Text file block still present
    assert any("notes.txt" in b.get("text", "") and "hello" in b.get("text", "")
               for b in content)


def test_detect_vision_support_returns_false_when_lookup_raises():
    """LiteLLM doesn't know every provider; raise → safe default of no vision."""
    from run import _detect_vision_support
    with patch("run.litellm") as mock_litellm:
        mock_litellm.supports_vision.side_effect = RuntimeError("unknown model")
        assert _detect_vision_support("exotic-provider/whatever") is False


def test_detect_vision_support_passes_through_truthy_result():
    from run import _detect_vision_support
    with patch("run.litellm") as mock_litellm:
        mock_litellm.supports_vision.return_value = True
        assert _detect_vision_support("anthropic/claude-sonnet-4-20250514") is True


def test_detect_vision_support_passes_through_falsy_result():
    from run import _detect_vision_support
    with patch("run.litellm") as mock_litellm:
        mock_litellm.supports_vision.return_value = False
        assert _detect_vision_support("openai/gpt-3.5-turbo") is False


@pytest.mark.asyncio
async def test_describe_image_cache_persists_across_worker_calls(image_file):
    """describe_image is cached by content hash, so re-sending the same image
    in a second turn does not re-call the vision model."""
    import src.llm as llm_module
    llm_module._description_cache.clear()

    captured: dict = {}
    fake = _fake_agent(captured, supports_vision=False, vision_model="vision/m")

    # We patch litellm.acompletion (which describe_image calls into) so we can
    # observe how many real LLM round-trips happen.
    from unittest.mock import MagicMock
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(
        content="cached description", tool_calls=None,
    ))]

    with patch("src.llm.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)

        for _ in range(2):
            in_q: asyncio.Queue = asyncio.Queue()
            out_q: asyncio.Queue = asyncio.Queue()
            msg = IncomingMessage(
                content="again", channel="cli", session_id="cli",
                reply_address={},
                attachments=[_att(image_file, "image/png")],
            )
            await in_q.put(msg)
            await _run_worker_once(in_q, out_q, fake)

        assert mock_litellm.acompletion.await_count == 1

    llm_module._description_cache.clear()




def test_large_text_attachment_spills_to_sidecar(tmp_path):
    from run import build_multimodal_content, _INLINE_TEXT_CAP
    big = tmp_path / "big.txt"
    body = "A" * (_INLINE_TEXT_CAP + 5000)
    big.write_text(body)

    blocks = build_multimodal_content("read this", [_att(big, "text/plain")])
    att_block = blocks[1]["text"]

    # Only the capped preview is inlined, not the full body.
    assert "A" * _INLINE_TEXT_CAP in att_block
    assert "A" * (_INLINE_TEXT_CAP + 1) not in att_block
    # Sidecar holds the full extracted text and is pointed at in the block.
    sidecar = str(big) + ".extracted.txt"
    assert sidecar in att_block
    assert os.path.exists(sidecar)
    with open(sidecar) as f:
        assert f.read() == body


def test_small_text_attachment_stays_fully_inline(tmp_path):
    from run import build_multimodal_content
    small = tmp_path / "small.txt"
    small.write_text("just a little")
    blocks = build_multimodal_content("x", [_att(small, "text/plain")])
    assert "just a little" in blocks[1]["text"]
    assert not os.path.exists(str(small) + ".extracted.txt")
