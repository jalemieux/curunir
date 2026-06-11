from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.channels._email_state import EmailState, PendingReply


def test_load_missing_file_returns_blank(tmp_path: Path):
    state = EmailState.load(tmp_path / "state.json")
    assert state.watermark_created_at is None
    assert state.watermark_message_id == ""


def test_load_corrupt_file_returns_blank(tmp_path: Path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    state = EmailState.load(p)
    assert state.watermark_created_at is None


def test_save_then_load_roundtrip(tmp_path: Path):
    p = tmp_path / "state.json"
    state = EmailState.load(p)
    ts = datetime(2026, 5, 14, 15, 30, 0, tzinfo=timezone.utc)
    state.set_watermark(ts, "msg-123")
    state.save()

    reloaded = EmailState.load(p)
    assert reloaded.watermark_created_at == ts
    assert reloaded.watermark_message_id == "msg-123"


def test_is_after_watermark_tuple_compare(tmp_path: Path):
    state = EmailState.load(tmp_path / "state.json")
    base = datetime(2026, 5, 14, 15, 30, 0, tzinfo=timezone.utc)
    state.set_watermark(base, "msg-100")

    older = datetime(2026, 5, 14, 15, 29, 0, tzinfo=timezone.utc)
    same = base
    newer = datetime(2026, 5, 14, 15, 31, 0, tzinfo=timezone.utc)

    assert not state.is_after_watermark(older, "msg-200")
    assert not state.is_after_watermark(same, "msg-099")  # same ts, lower id
    assert state.is_after_watermark(same, "msg-200")      # same ts, higher id
    assert state.is_after_watermark(newer, "msg-001")


def test_is_after_watermark_when_blank(tmp_path: Path):
    state = EmailState.load(tmp_path / "state.json")
    ts = datetime(2026, 5, 14, 15, 30, 0, tzinfo=timezone.utc)
    assert state.is_after_watermark(ts, "any")


# --- Boot-state classifier: first-run vs corrupt -------------------------

def test_load_missing_file_is_not_corrupt(tmp_path: Path):
    """A genuine first run (no file) must be distinguishable from corruption."""
    state = EmailState.load(tmp_path / "state.json")
    assert state.corrupt is False
    assert state.cursor_created_at is None


def test_load_corrupt_file_sets_corrupt_flag(tmp_path: Path):
    """Truncated/garbage JSON must flag corrupt=True, not collapse to first-run."""
    p = tmp_path / "state.json"
    p.write_text("{not json")
    state = EmailState.load(p)
    assert state.corrupt is True
    assert state.cursor_created_at is None


def test_load_valid_file_is_not_corrupt(tmp_path: Path):
    p = tmp_path / "state.json"
    state = EmailState.load(p)
    state.set_cursor(datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc), "m1")
    state.save()
    reloaded = EmailState.load(p)
    assert reloaded.corrupt is False
    assert reloaded.cursor_message_id == "m1"


# --- Migration from the legacy {watermark_*} format ----------------------

def test_migration_from_legacy_watermark_format(tmp_path: Path):
    """An old file written before the cursor rename must load as cursor."""
    p = tmp_path / "state.json"
    p.write_text(
        '{"watermark_created_at": "2026-05-14T15:30:00+00:00", '
        '"watermark_message_id": "legacy-1"}'
    )
    state = EmailState.load(p)
    assert state.corrupt is False
    assert state.cursor_created_at == datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc)
    assert state.cursor_message_id == "legacy-1"
    assert state.pending == {}


def test_watermark_aliases_proxy_to_cursor(tmp_path: Path):
    """Legacy in-memory API (set_watermark / watermark_*) still works."""
    state = EmailState.load(tmp_path / "state.json")
    ts = datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc)
    state.set_watermark(ts, "msg-9")
    assert state.cursor_created_at == ts
    assert state.cursor_message_id == "msg-9"
    assert state.watermark_created_at == ts
    assert state.watermark_message_id == "msg-9"
    assert state.is_after_watermark(ts, "msg-zzz") == state.is_after_cursor(ts, "msg-zzz")


# --- Pending-reply ledger ------------------------------------------------

def _reply_addr(mid="in-1"):
    return {"to": "alice@example.com", "subject": "Re: hi", "in_reply_to": mid}


def test_add_pending_records_queued_entry(tmp_path: Path):
    state = EmailState.load(tmp_path / "state.json")
    ts = datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc)
    state.add_pending("in-1", created_at=ts, thread_id="t1", reply_address=_reply_addr())
    assert "in-1" in state.pending
    pr = state.pending["in-1"]
    assert isinstance(pr, PendingReply)
    assert pr.status == "queued"
    assert pr.attempts == 0
    assert pr.thread_id == "t1"
    assert pr.reply_address["in_reply_to"] == "in-1"


def test_ack_removes_pending_entry(tmp_path: Path):
    state = EmailState.load(tmp_path / "state.json")
    state.add_pending("in-1", created_at=None, thread_id="t1", reply_address=_reply_addr())
    state.ack("in-1")
    assert "in-1" not in state.pending
    # Acking an unknown id is a no-op, not an error.
    state.ack("nope")


def test_mark_retry_stores_payload_and_bumps_attempts(tmp_path: Path):
    state = EmailState.load(tmp_path / "state.json")
    state.add_pending("in-1", created_at=None, thread_id="t1", reply_address=_reply_addr())
    next_at = datetime(2026, 5, 14, 15, 40, tzinfo=timezone.utc)
    payload = {"text_body": "hi", "html_body": "<p>hi</p>", "attachment_paths": []}
    state.mark_retry("in-1", reply_payload=payload, next_retry_at=next_at, error="boom")
    pr = state.pending["in-1"]
    assert pr.status == "retry"
    assert pr.attempts == 1
    assert pr.reply_payload == payload
    assert pr.next_retry_at == next_at
    # A second failure bumps attempts again.
    state.mark_retry("in-1", reply_payload=payload, next_retry_at=next_at, error="boom2")
    assert state.pending["in-1"].attempts == 2


def test_mark_dead_sets_status(tmp_path: Path):
    state = EmailState.load(tmp_path / "state.json")
    state.add_pending("in-1", created_at=None, thread_id="t1", reply_address=_reply_addr())
    state.mark_dead("in-1")
    assert state.pending["in-1"].status == "dead"


def test_due_retries_filters_by_time_and_status(tmp_path: Path):
    state = EmailState.load(tmp_path / "state.json")
    now = datetime(2026, 5, 14, 16, 0, tzinfo=timezone.utc)
    payload = {"text_body": "hi"}
    # Due (next_retry_at in the past).
    state.add_pending("due", created_at=None, thread_id="t", reply_address=_reply_addr("due"))
    state.mark_retry("due", reply_payload=payload, next_retry_at=now - timedelta(minutes=1))
    # Not yet due.
    state.add_pending("later", created_at=None, thread_id="t", reply_address=_reply_addr("later"))
    state.mark_retry("later", reply_payload=payload, next_retry_at=now + timedelta(minutes=5))
    # Still queued (never failed) — not a retry candidate.
    state.add_pending("queued", created_at=None, thread_id="t", reply_address=_reply_addr("queued"))
    # Dead — not a retry candidate.
    state.add_pending("dead", created_at=None, thread_id="t", reply_address=_reply_addr("dead"))
    state.mark_retry("dead", reply_payload=payload, next_retry_at=now - timedelta(minutes=1))
    state.mark_dead("dead")

    due_ids = [mid for mid, _ in state.due_retries(now)]
    assert due_ids == ["due"]


def test_pending_ledger_survives_save_load_roundtrip(tmp_path: Path):
    p = tmp_path / "state.json"
    state = EmailState.load(p)
    state.set_cursor(datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc), "cur-1")
    ts = datetime(2026, 5, 14, 15, 29, tzinfo=timezone.utc)
    next_at = datetime(2026, 5, 14, 15, 45, tzinfo=timezone.utc)
    state.add_pending("in-1", created_at=ts, thread_id="t1", reply_address=_reply_addr("in-1"))
    state.mark_retry(
        "in-1",
        reply_payload={"text_body": "hi", "html_body": None, "attachment_paths": ["/tmp/a.pdf"]},
        next_retry_at=next_at,
    )
    state.add_pending("in-2", created_at=ts, thread_id="t2", reply_address=_reply_addr("in-2"))
    state.save()

    reloaded = EmailState.load(p)
    assert reloaded.cursor_message_id == "cur-1"
    assert set(reloaded.pending) == {"in-1", "in-2"}
    r1 = reloaded.pending["in-1"]
    assert r1.status == "retry"
    assert r1.attempts == 1
    assert r1.next_retry_at == next_at
    assert r1.reply_payload["attachment_paths"] == ["/tmp/a.pdf"]
    assert r1.created_at == ts
    assert reloaded.pending["in-2"].status == "queued"
