from datetime import datetime, timezone
from pathlib import Path

from src.channels._email_state import EmailState


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
