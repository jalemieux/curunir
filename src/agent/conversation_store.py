"""Durable per-conversation transcript storage.

Each conversation is one JSON file at ``context/conversations/<session_id>.json``::

    {session_id, title, preview, created_at, updated_at,
     last_extracted_at, archive_path, history: [...]}

This is the source of truth for conversation transcripts. The agent keeps
only *active* sessions in memory (``agent.sessions``) and lazy-loads a
transcript from here on demand; memory extraction reads transcripts from
here too, so a conversation no longer has to sit in memory to be summarized.

Writes are atomic (temp file + ``os.replace``) and read-modify-write:
``save()`` refreshes ``updated_at``/``history``/``title``/``preview`` while
preserving ``created_at``/``last_extracted_at``/``archive_path``, and
``set_extracted()`` stamps the extraction fields without touching history.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_TITLE_MAX = 60
_PREVIEW_MAX = 140

# A conversation is "settled" once this long has elapsed since its last turn;
# extraction waits for the lull so it doesn't summarize mid-conversation.
_EXTRACTION_IDLE_SEC = 300

# Minimum user/assistant turns before a conversation is worth extracting.
_MIN_TURNS = 2


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_session_id(session_id: str) -> str:
    """Reject session ids that would escape the conversations directory."""
    if not session_id or "/" in session_id or "\\" in session_id or session_id in (".", ".."):
        raise ValueError(f"unsafe session_id: {session_id!r}")
    return session_id


def conversations_dir(context_dir: Path) -> Path:
    return Path(context_dir) / "conversations"


def _path(context_dir: Path, session_id: str) -> Path:
    return conversations_dir(context_dir) / f"{_safe_session_id(session_id)}.json"


def _first_user_text(history: list[dict]) -> str:
    """Extract the typed text of the first user turn (handles multimodal)."""
    for entry in history:
        if entry.get("role") != "user":
            continue
        content = entry.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "") or ""
            return ""
        return content or ""
    return ""


def _derive_title(history: list[dict]) -> str:
    text = " ".join(_first_user_text(history).split())
    if not text:
        return "New conversation"
    return text[:_TITLE_MAX - 1] + "…" if len(text) > _TITLE_MAX else text


def _derive_preview(history: list[dict]) -> str:
    text = " ".join(_first_user_text(history).split())
    return text[:_PREVIEW_MAX - 1] + "…" if len(text) > _PREVIEW_MAX else text


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".conv.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return None


def save(context_dir: Path, session_id: str, history: list[dict], *,
         channel: str | None = None, now: datetime | None = None) -> None:
    """Persist a conversation transcript.

    Read-modify-write: ``created_at``/``channel`` are set once and preserved;
    extraction metadata (``last_extracted_at``, ``archive_path``) carried over
    from any existing file so a turn-completion save never clobbers it.

    ``channel`` records the originating channel (``"portal"``, ``"email"``,
    ``"ws"``, …) so consumers can scope the conversation list — the portal
    sidebar shows only portal conversations.
    """
    now = now or _now()
    path = _path(context_dir, session_id)
    existing = _read(path) or {}
    record = {
        "session_id": session_id,
        "title": _derive_title(history),
        "preview": _derive_preview(history),
        "channel": channel or existing.get("channel"),
        "created_at": existing.get("created_at") or now.isoformat(),
        "updated_at": now.isoformat(),
        "last_extracted_at": existing.get("last_extracted_at"),
        "archive_path": existing.get("archive_path"),
        "history": history,
    }
    _atomic_write(path, record)


def set_extracted(context_dir: Path, session_id: str, archive_path: str | None, *,
                  now: datetime | None = None) -> None:
    """Stamp extraction metadata after a summary is written, preserving history."""
    now = now or _now()
    path = _path(context_dir, session_id)
    record = _read(path)
    if record is None:
        return
    record["last_extracted_at"] = now.isoformat()
    record["archive_path"] = str(archive_path) if archive_path is not None else None
    _atomic_write(path, record)


def load(context_dir: Path, session_id: str) -> dict | None:
    """Return the full conversation record (including history), or None."""
    return _read(_path(context_dir, session_id))


def _infer_channel(session_id: str, title: str) -> str:
    """Best-effort channel for records saved before ``channel`` was stored.

    Email turns prefix their content with ``[channel: email, …]`` and the
    title derives from that first user turn, so the prefix is a reliable
    tell. The fixed CLI session id is ``"cli"``. Everything else (portal
    per-tab UUIDs, the legacy ``"portal"`` id) is treated as portal so a
    real portal conversation is never hidden by the inference.
    """
    if title.startswith("[channel: email"):
        return "email"
    if session_id == "cli":
        return "cli"
    return "portal"


def list_conversations(context_dir: Path, *,
                       channel: str | None = None) -> list[dict]:
    """Return metadata-only summaries, newest ``updated_at`` first.

    When ``channel`` is given, only conversations from that channel are
    returned. Records saved before ``channel`` was persisted fall back to
    ``_infer_channel`` so the filter still works on legacy transcripts.
    """
    cdir = conversations_dir(context_dir)
    if not cdir.is_dir():
        return []
    out: list[dict] = []
    for path in cdir.glob("*.json"):
        record = _read(path)
        if record is None:
            continue
        session_id = record.get("session_id", path.stem)
        title = record.get("title", "")
        chan = record.get("channel") or _infer_channel(session_id, title)
        if channel is not None and chan != channel:
            continue
        out.append({
            "session_id": session_id,
            "title": title,
            "preview": record.get("preview", ""),
            "channel": chan,
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        })
    out.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
    return out


def delete(context_dir: Path, session_id: str) -> bool:
    """Remove a conversation file. Returns True if it existed."""
    path = _path(context_dir, session_id)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def _turn_count(history: list[dict]) -> int:
    return sum(1 for e in history if e.get("role") in ("user", "assistant"))


def due_for_extraction(context_dir: Path, *, now: datetime | None = None) -> list[str]:
    """Session ids whose transcripts are ready for memory extraction.

    Gate (all must hold): more than ``_MIN_TURNS`` user/assistant turns; the
    conversation has been idle at least ``_EXTRACTION_IDLE_SEC``; and there is
    new content since the last extraction (``updated_at > last_extracted_at``).
    """
    now = now or _now()
    cdir = conversations_dir(context_dir)
    if not cdir.is_dir():
        return []
    due: list[str] = []
    for path in sorted(cdir.glob("*.json")):
        record = _read(path)
        if record is None:
            continue
        if _turn_count(record.get("history", [])) <= _MIN_TURNS:
            continue
        updated_raw = record.get("updated_at")
        if not updated_raw:
            continue
        try:
            updated = datetime.fromisoformat(updated_raw)
        except ValueError:
            continue
        if (now - updated).total_seconds() < _EXTRACTION_IDLE_SEC:
            continue
        extracted_raw = record.get("last_extracted_at")
        if extracted_raw and extracted_raw >= updated_raw:
            continue
        due.append(record.get("session_id", path.stem))
    return due
