"""Tests for src/agent/conversation_store.py — durable transcript storage."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.agent import conversation_store as cs


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_save_then_load_round_trips_history(tmp_context):
    history = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]
    cs.save(tmp_context, "sess-1", history)

    record = cs.load(tmp_context, "sess-1")
    assert record is not None
    assert record["session_id"] == "sess-1"
    assert record["history"] == history


def test_save_creates_conversations_directory(tmp_context):
    assert not (tmp_context / "conversations").exists()
    cs.save(tmp_context, "sess-1", [{"role": "user", "content": "hi"}])
    assert (tmp_context / "conversations" / "sess-1.json").is_file()


def test_saved_file_is_valid_json(tmp_context):
    cs.save(tmp_context, "sess-1", [{"role": "user", "content": "hi"}])
    raw = (tmp_context / "conversations" / "sess-1.json").read_text()
    parsed = json.loads(raw)
    assert parsed["session_id"] == "sess-1"


def test_load_missing_conversation_returns_none(tmp_context):
    assert cs.load(tmp_context, "nope") is None


def test_title_and_preview_derived_from_first_user_turn(tmp_context):
    history = [
        {"role": "user", "content": "What is the capital of France?"},
        {"role": "assistant", "content": "Paris."},
        {"role": "user", "content": "And Germany?"},
    ]
    cs.save(tmp_context, "sess-1", history)
    record = cs.load(tmp_context, "sess-1")
    assert record["title"] == "What is the capital of France?"
    assert record["preview"] == "What is the capital of France?"


def test_title_truncated_for_long_first_turn(tmp_context):
    long_msg = "word " * 50
    cs.save(tmp_context, "sess-1", [{"role": "user", "content": long_msg}])
    record = cs.load(tmp_context, "sess-1")
    assert len(record["title"]) <= 60


def test_title_from_multimodal_first_user_turn(tmp_context):
    history = [
        {"role": "user", "content": [
            {"type": "text", "text": "Describe this image"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ]},
    ]
    cs.save(tmp_context, "sess-1", history)
    record = cs.load(tmp_context, "sess-1")
    assert record["title"] == "Describe this image"


def test_list_conversations_orders_by_updated_at_newest_first(tmp_context):
    cs.save(tmp_context, "old", [{"role": "user", "content": "old"}],
            now=_utc(2026, 5, 1, 12, 0, 0))
    cs.save(tmp_context, "new", [{"role": "user", "content": "new"}],
            now=_utc(2026, 5, 10, 12, 0, 0))
    cs.save(tmp_context, "mid", [{"role": "user", "content": "mid"}],
            now=_utc(2026, 5, 5, 12, 0, 0))

    ids = [c["session_id"] for c in cs.list_conversations(tmp_context)]
    assert ids == ["new", "mid", "old"]


def test_list_conversations_excludes_history(tmp_context):
    cs.save(tmp_context, "sess-1", [{"role": "user", "content": "hi"}])
    summaries = cs.list_conversations(tmp_context)
    assert len(summaries) == 1
    assert "history" not in summaries[0]
    assert {"session_id", "title", "preview", "created_at", "updated_at"} <= set(summaries[0])


def test_list_conversations_empty_when_no_directory(tmp_context):
    assert cs.list_conversations(tmp_context) == []


def test_save_persists_channel(tmp_context):
    cs.save(tmp_context, "sess-1", [{"role": "user", "content": "hi"}],
            channel="portal")
    assert cs.load(tmp_context, "sess-1")["channel"] == "portal"


def test_channel_preserved_across_resave(tmp_context):
    cs.save(tmp_context, "sess-1", [{"role": "user", "content": "hi"}],
            channel="email")
    cs.save(tmp_context, "sess-1", [{"role": "user", "content": "hi again"}])
    assert cs.load(tmp_context, "sess-1")["channel"] == "email"


def test_list_conversations_filters_by_channel(tmp_context):
    cs.save(tmp_context, "p", [{"role": "user", "content": "portal one"}],
            channel="portal")
    cs.save(tmp_context, "e", [{"role": "user", "content": "email one"}],
            channel="email")
    ids = [c["session_id"] for c in cs.list_conversations(tmp_context, channel="portal")]
    assert ids == ["p"]


def test_list_conversations_infers_email_channel_for_legacy_records(tmp_context):
    """Pre-channel records: an [channel: email …] title prefix marks email."""
    cs.save(tmp_context, "thread-123", [
        {"role": "user", "content": "[channel: email, from: a@b.com]\nhello"},
    ])
    [conv] = cs.list_conversations(tmp_context)
    assert conv["channel"] == "email"
    assert cs.list_conversations(tmp_context, channel="portal") == []


def test_list_conversations_legacy_record_defaults_to_portal(tmp_context):
    cs.save(tmp_context, "uuid-abc", [{"role": "user", "content": "hi"}])
    [conv] = cs.list_conversations(tmp_context, channel="portal")
    assert conv["channel"] == "portal"


def test_save_preserves_created_at_across_resave(tmp_context):
    cs.save(tmp_context, "sess-1", [{"role": "user", "content": "first"}],
            now=_utc(2026, 5, 1, 9, 0, 0))
    cs.save(tmp_context, "sess-1", [{"role": "user", "content": "first"},
                                    {"role": "assistant", "content": "ok"}],
            now=_utc(2026, 5, 2, 9, 0, 0))
    record = cs.load(tmp_context, "sess-1")
    assert record["created_at"] == _utc(2026, 5, 1, 9, 0, 0).isoformat()
    assert record["updated_at"] == _utc(2026, 5, 2, 9, 0, 0).isoformat()


def test_save_preserves_extraction_metadata(tmp_context):
    cs.save(tmp_context, "sess-1", [{"role": "user", "content": "hi"}])
    cs.set_extracted(tmp_context, "sess-1", "memory/archives/conversations/x.md",
                     now=_utc(2026, 5, 3, 10, 0, 0))
    # A later turn re-saves the transcript.
    cs.save(tmp_context, "sess-1", [{"role": "user", "content": "hi"},
                                    {"role": "assistant", "content": "yo"}])
    record = cs.load(tmp_context, "sess-1")
    assert record["last_extracted_at"] == _utc(2026, 5, 3, 10, 0, 0).isoformat()
    assert record["archive_path"] == "memory/archives/conversations/x.md"


def test_set_extracted_preserves_history(tmp_context):
    history = [{"role": "user", "content": "hi"},
               {"role": "assistant", "content": "yo"}]
    cs.save(tmp_context, "sess-1", history)
    cs.set_extracted(tmp_context, "sess-1", "memory/archives/conversations/x.md")
    record = cs.load(tmp_context, "sess-1")
    assert record["history"] == history


def test_delete_removes_the_file(tmp_context):
    cs.save(tmp_context, "sess-1", [{"role": "user", "content": "hi"}])
    assert cs.delete(tmp_context, "sess-1") is True
    assert cs.load(tmp_context, "sess-1") is None


def test_delete_missing_conversation_returns_false(tmp_context):
    assert cs.delete(tmp_context, "nope") is False


def test_rejects_session_id_with_path_traversal(tmp_context):
    with pytest.raises(ValueError):
        cs.save(tmp_context, "../escape", [{"role": "user", "content": "x"}])
    with pytest.raises(ValueError):
        cs.load(tmp_context, "a/b")


# --- due_for_extraction: the 60s-loop gate rule ---------------------------
#   extract if  turn_count > 2  AND  idle >= 5 min  AND  updated_at > last_extracted_at

def _grown_history():
    """3 user/assistant turns — passes the turn_count > 2 gate."""
    return [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]


def test_due_includes_settled_grown_unextracted_conversation(tmp_context):
    cs.save(tmp_context, "sess-1", _grown_history(), now=_utc(2026, 5, 10, 12, 0, 0))
    now = _utc(2026, 5, 10, 12, 10, 0)  # 10 min later → settled
    assert cs.due_for_extraction(tmp_context, now=now) == ["sess-1"]


def test_due_excludes_recently_active_conversation(tmp_context):
    cs.save(tmp_context, "sess-1", _grown_history(), now=_utc(2026, 5, 10, 12, 0, 0))
    now = _utc(2026, 5, 10, 12, 2, 0)  # only 2 min later → still active
    assert cs.due_for_extraction(tmp_context, now=now) == []


def test_due_excludes_conversation_with_no_new_content(tmp_context):
    cs.save(tmp_context, "sess-1", _grown_history(), now=_utc(2026, 5, 10, 12, 0, 0))
    cs.set_extracted(tmp_context, "sess-1", "x.md", now=_utc(2026, 5, 10, 12, 1, 0))
    now = _utc(2026, 5, 10, 12, 30, 0)  # settled, but already extracted
    assert cs.due_for_extraction(tmp_context, now=now) == []


def test_due_reincludes_conversation_after_it_grows_again(tmp_context):
    cs.save(tmp_context, "sess-1", _grown_history(), now=_utc(2026, 5, 10, 12, 0, 0))
    cs.set_extracted(tmp_context, "sess-1", "x.md", now=_utc(2026, 5, 10, 12, 1, 0))
    cs.save(tmp_context, "sess-1", _grown_history() + [{"role": "assistant", "content": "a2"}],
            now=_utc(2026, 5, 10, 13, 0, 0))  # resumed → new content
    now = _utc(2026, 5, 10, 13, 10, 0)
    assert cs.due_for_extraction(tmp_context, now=now) == ["sess-1"]


def test_due_excludes_too_short_conversation(tmp_context):
    short = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    cs.save(tmp_context, "sess-1", short, now=_utc(2026, 5, 10, 12, 0, 0))
    now = _utc(2026, 5, 10, 13, 0, 0)
    assert cs.due_for_extraction(tmp_context, now=now) == []
