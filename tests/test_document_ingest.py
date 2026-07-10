# tests/test_document_ingest.py
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from src.document_ingest import DocumentIngestError, ingest_document
from src.llm import LLMResponse, LLMUsage
from src.usage_store import UsageStore

CARD = "# Document card\n\n- Type: memo\n- Sections: lines 1-3"


def _make_doc(tmp_path, name="report.txt", lines=("alpha", "beta", "gamma")):
    doc = tmp_path / name
    doc.write_text("\n".join(lines))
    return doc


async def test_ingest_writes_card_file_and_returns_text(tmp_path, agent_config):
    doc = _make_doc(tmp_path)
    with patch("src.document_ingest.call_llm", new_callable=AsyncMock) as llm:
        llm.return_value = LLMResponse(text=CARD, tool_calls=None)
        card = await ingest_document(doc, agent_config)

    assert card == CARD
    card_file = tmp_path / "report.txt.card.md"
    assert card_file.exists()
    assert card_file.read_text() == CARD
    assert llm.await_count == 1


async def test_prompt_contains_line_numbered_document(tmp_path, agent_config):
    doc = _make_doc(tmp_path, lines=("alpha", "beta"))
    with patch("src.document_ingest.call_llm", new_callable=AsyncMock) as llm:
        llm.return_value = LLMResponse(text=CARD, tool_calls=None)
        await ingest_document(doc, agent_config)

    messages = llm.await_args.args[1]
    user_content = messages[-1]["content"]
    assert "1\talpha" in user_content
    assert "2\tbeta" in user_content
    assert "report.txt" in user_content


async def test_existing_card_short_circuits_llm(tmp_path, agent_config):
    doc = _make_doc(tmp_path)
    (tmp_path / "report.txt.card.md").write_text("cached card")
    with patch("src.document_ingest.call_llm", new_callable=AsyncMock) as llm:
        card = await ingest_document(doc, agent_config)

    assert card == "cached card"
    llm.assert_not_awaited()


async def test_missing_file_raises(tmp_path, agent_config):
    with pytest.raises(DocumentIngestError, match="not found"):
        await ingest_document(tmp_path / "nope.txt", agent_config)


async def test_empty_file_raises(tmp_path, agent_config):
    doc = tmp_path / "empty.txt"
    doc.write_text("")
    with pytest.raises(DocumentIngestError, match="empty"):
        await ingest_document(doc, agent_config)


async def test_image_file_raises(tmp_path, agent_config):
    doc = tmp_path / "scan.png"
    doc.write_bytes(b"\x89PNG fake")
    with pytest.raises(DocumentIngestError, match="image"):
        await ingest_document(doc, agent_config)


async def test_empty_llm_response_raises_and_writes_no_card(tmp_path, agent_config):
    doc = _make_doc(tmp_path)
    with patch("src.document_ingest.call_llm", new_callable=AsyncMock) as llm:
        llm.return_value = LLMResponse(text=None, tool_calls=None)
        with pytest.raises(DocumentIngestError, match="empty"):
            await ingest_document(doc, agent_config)

    assert not (tmp_path / "report.txt.card.md").exists()


async def test_usage_recorded_under_ingest_session(tmp_path, agent_config):
    doc = _make_doc(tmp_path)
    store = UsageStore(tmp_path / "usage.db")
    usage = LLMUsage(prompt_tokens=100, completion_tokens=20, model="test-model")
    with patch("src.document_ingest.call_llm", new_callable=AsyncMock) as llm:
        llm.return_value = LLMResponse(text=CARD, tool_calls=None, usage=usage)
        await ingest_document(doc, agent_config, usage_store=store)

    rows = sqlite3.connect(tmp_path / "usage.db").execute(
        "SELECT session_id, prompt_tokens, model FROM usage"
    ).fetchall()
    assert len(rows) == 1
    session_id, prompt_tokens, model = rows[0]
    assert session_id.startswith("ingest:")
    assert prompt_tokens == 100
    assert model == "test-model"


async def test_oversize_document_uses_map_reduce(tmp_path, agent_config):
    # 40 lines of ~10 chars; cap at 200 chars forces multiple chunks + merge.
    doc = _make_doc(tmp_path, lines=[f"line-{i:04d}" for i in range(40)])
    with (
        patch("src.document_ingest.call_llm", new_callable=AsyncMock) as llm,
        patch("src.document_ingest.MAX_ONESHOT_CHARS", 200),
    ):
        llm.return_value = LLMResponse(text=CARD, tool_calls=None)
        card = await ingest_document(doc, agent_config)

    assert card == CARD
    # n chunk calls + 1 merge call
    assert llm.await_count > 2
    # Chunk prompts keep absolute line numbers (numbering happens pre-split).
    later_chunk_prompt = llm.await_args_list[1].args[1][-1]["content"]
    assert "line-0000" not in later_chunk_prompt


async def test_skill_card_spec_injected_without_frontmatter(tmp_path, agent_config):
    skill_dir = agent_config.skill_dirs[0] / "document-ingest"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: document-ingest\ndescription: routing text for the agent\n---\n"
        "# Document card: <filename>\n- **Type:** ...\n"
    )
    doc = _make_doc(tmp_path)
    with patch("src.document_ingest.call_llm", new_callable=AsyncMock) as llm:
        llm.return_value = LLMResponse(text=CARD, tool_calls=None)
        await ingest_document(doc, agent_config)

    system = llm.await_args.args[1][0]["content"]
    assert "# Document card: <filename>" in system
    assert "routing text for the agent" not in system


async def test_hash_dedup_reuses_card_for_identical_bytes(tmp_path, agent_config):
    """Same bytes at a different path (re-upload, re-stage) never re-ingest."""
    doc_a = tmp_path / "a" / "report.txt"
    doc_a.parent.mkdir()
    doc_a.write_text("identical body " * 10)
    doc_b = tmp_path / "b" / "renamed.txt"
    doc_b.parent.mkdir()
    doc_b.write_text("identical body " * 10)

    with patch("src.document_ingest.call_llm", new_callable=AsyncMock) as llm:
        llm.return_value = LLMResponse(text=CARD, tool_calls=None)
        card_a = await ingest_document(doc_a, agent_config)
        card_b = await ingest_document(doc_b, agent_config)

    assert llm.await_count == 1                      # second ingest was free
    assert card_b == card_a == CARD
    # The reused card also lands as B's sibling so the read gate finds it.
    assert (tmp_path / "b" / "renamed.txt.card.md").read_text() == CARD


async def test_ingest_writes_hash_store_copy(tmp_path, agent_config):
    import hashlib
    doc = _make_doc(tmp_path)
    with patch("src.document_ingest.call_llm", new_callable=AsyncMock) as llm:
        llm.return_value = LLMResponse(text=CARD, tool_calls=None)
        await ingest_document(doc, agent_config)

    digest = hashlib.sha256(doc.read_bytes()).hexdigest()
    stored = agent_config.context_dir / "cards" / f"{digest}.card.md"
    assert stored.read_text() == CARD


async def test_different_bytes_are_not_deduped(tmp_path, agent_config):
    doc_a = _make_doc(tmp_path, name="a.txt", lines=("one", "two"))
    doc_b = _make_doc(tmp_path, name="b.txt", lines=("three", "four"))
    with patch("src.document_ingest.call_llm", new_callable=AsyncMock) as llm:
        llm.return_value = LLMResponse(text=CARD, tool_calls=None)
        await ingest_document(doc_a, agent_config)
        await ingest_document(doc_b, agent_config)

    assert llm.await_count == 2


async def test_empty_hash_store_entry_is_ignored(tmp_path, agent_config):
    import hashlib
    doc = _make_doc(tmp_path)
    digest = hashlib.sha256(doc.read_bytes()).hexdigest()
    store = agent_config.context_dir / "cards" / f"{digest}.card.md"
    store.parent.mkdir(parents=True)
    store.write_text("  \n")

    with patch("src.document_ingest.call_llm", new_callable=AsyncMock) as llm:
        llm.return_value = LLMResponse(text=CARD, tool_calls=None)
        card = await ingest_document(doc, agent_config)

    assert card == CARD
    assert llm.await_count == 1


async def test_card_short_circuit_ignores_empty_card_file(tmp_path, agent_config):
    doc = _make_doc(tmp_path)
    (tmp_path / "report.txt.card.md").write_text("")
    with patch("src.document_ingest.call_llm", new_callable=AsyncMock) as llm:
        llm.return_value = LLMResponse(text=CARD, tool_calls=None)
        card = await ingest_document(doc, agent_config)

    assert card == CARD
    assert llm.await_count == 1
