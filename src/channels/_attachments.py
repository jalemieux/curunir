"""WebSocket-flavored attachment helpers, shared between channels.

Extracted from src/channels/ws.py so the new PortalChannel can reuse
the exact same validation, decoding, staging, filename normalization,
and outbound enrichment without duplication.
"""

import base64
import os
import re
import uuid as _uuid

# Regex matching any Unicode whitespace character that isn't a regular space.
_UNICODE_WHITESPACE_RE = re.compile(r'[^\S ]+')

# Allowed shape for a client-supplied session id. Covers the legitimate
# producers (`cli`, `portal`, uuid4 hex, gmail thread ids, browser tab ids
# like `tab-42`) while rejecting anything that could escape `uploads_dir`
# when joined into a filesystem path.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-:]{1,128}$")


def _sanitize_session_id(sid: object) -> str:
    """Validate a client-supplied session id used as a filesystem subdir.

    Returns the validated id, or raises ``ValueError`` for anything that
    isn't a non-empty string of [A-Za-z0-9_-:] (max 128 chars). Used at
    every channel boundary that lets the client name its session, since
    that id is composed into `<uploads_dir>/<session_id>/...` paths.
    """
    if not isinstance(sid, str):
        raise ValueError(f"session_id must be a string, got {type(sid).__name__}")
    if sid in (".", ".."):
        raise ValueError(f"session_id rejected: {sid!r}")
    if not _SESSION_ID_RE.match(sid):
        raise ValueError(f"session_id rejected: {sid!r}")
    return sid


def _normalize_unicode_whitespace(s: str) -> str:
    """Replace Unicode whitespace characters (e.g. \\u202f) with regular spaces.

    LLMs convert exotic whitespace to regular spaces when generating tool
    calls, so attachment filenames need normalizing on intake to avoid
    file-not-found errors downstream.
    """
    return _UNICODE_WHITESPACE_RE.sub(' ', s)

# Size caps (mirrored in cli.py)
_MAX_IMAGE_BYTES = 5 * 1024 * 1024          # 5 MB
_MAX_TEXT_BYTES = 256 * 1024                # 256 KB
_MAX_DOC_BYTES = 10 * 1024 * 1024           # 10 MB (PDF, DOCX)
_MAX_TOTAL_BYTES = 20 * 1024 * 1024         # 20 MB
_ALLOWED_IMAGE_MIMES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
})
_ALLOWED_DOC_MIMES = frozenset({
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
})

_MAX_ATTACHMENT_CONTENT_SIZE = 512 * 1024  # 512KB


def _validate_attachment_metadata(mime: str, size: int) -> str | None:
    """Channel-agnostic mime + size policy. Returns None if accepted,
    or a human-readable rejection reason. Used by every inbound channel
    so they all enforce the same allowlist (images, PDF, DOCX, UTF-8 text).
    """
    if mime.startswith("image/"):
        if mime not in _ALLOWED_IMAGE_MIMES:
            return f"unsupported image type {mime}"
        if size > _MAX_IMAGE_BYTES:
            return f"{size} bytes exceeds 5 MB image cap"
    elif mime in _ALLOWED_DOC_MIMES:
        if size > _MAX_DOC_BYTES:
            return f"{size} bytes exceeds 10 MB document cap"
    elif mime.startswith("text/") or mime == "application/json":
        if size > _MAX_TEXT_BYTES:
            return f"{size} bytes exceeds 256 KB text cap"
    else:
        return f"unsupported mime type {mime}"
    return None


def _attach_download_data(att: dict, path: str) -> None:
    """Populate ``att["data"]`` (base64 bytes) when the file is within
    download caps for its mime type. Sets ``att["error"]`` if the file
    is missing. Leaves ``data`` absent for oversized files or unsupported
    mimes.

    `path` is the original (possibly absolute) filesystem path — caller
    must pass this BEFORE rewriting ``att["path"]`` to a relative form.
    """
    mime = att.get("mime_type", "")
    if mime in _ALLOWED_IMAGE_MIMES:
        cap = _MAX_IMAGE_BYTES
    elif mime in _ALLOWED_DOC_MIMES:
        cap = _MAX_DOC_BYTES
    elif mime.startswith("text/") or mime == "application/json":
        cap = _MAX_TEXT_BYTES
    else:
        return

    if not os.path.isfile(path):
        att["error"] = "file not found"
        return

    if os.path.getsize(path) > cap:
        return

    try:
        with open(path, "rb") as f:
            att["data"] = base64.b64encode(f.read()).decode("ascii")
    except OSError:
        att["error"] = "file not found"


def _enrich_attachments(attachments: list[dict], project_root: str) -> None:
    """Inline content and normalize paths for outbound attachments in-place.

    Text/JSON files under the size cap are read into `att["content"]` so the
    UI can render them without a second fetch. Non-text or oversized files
    get `content=None`; missing files additionally get `error="file not found"`.
    Absolute paths are rewritten relative to `project_root` for display.
    """
    for att in attachments:
        path = att["path"]
        mime = att.get("mime_type", "")
        is_text = mime.startswith("text/") or mime == "application/json"

        if os.path.isabs(path):
            try:
                att["path"] = os.path.relpath(path, project_root)
            except ValueError:
                pass  # different drive on Windows, keep absolute

        _attach_download_data(att, path)

        if not is_text:
            att["content"] = None
            continue

        if not os.path.isfile(path):
            att["content"] = None
            att["error"] = "file not found"
            continue

        if os.path.getsize(path) > _MAX_ATTACHMENT_CONTENT_SIZE:
            att["content"] = None
            continue

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                att["content"] = f.read()
        except OSError:
            att["content"] = None
            att["error"] = "file not found"


def _decode_attachments(raw: list | None) -> tuple[list[dict] | None, str | None]:
    """Validate and base64-decode inbound attachment payloads.

    Returns (decoded_items, None) on success, or (None, error_str) on failure.
    A decoded item is {"filename": str, "mime_type": str, "bytes": bytes}.
    No disk I/O here — callers stage the bytes separately.
    """
    if raw is None:
        return [], None
    if not isinstance(raw, list):
        return None, "attachments must be a list"

    decoded: list[dict] = []
    total_bytes = 0
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return None, f"attachment[{i}] is not an object"
        for key in ("filename", "mime_type", "data"):
            if key not in item or not isinstance(item[key], str):
                return None, f"attachment[{i}] missing or invalid '{key}'"

        filename = item["filename"]
        mime = item["mime_type"]
        try:
            payload = base64.b64decode(item["data"], validate=True)
        except (ValueError, base64.binascii.Error):
            return None, f"attachment[{i}] '{filename}': invalid base64"

        size = len(payload)

        reason = _validate_attachment_metadata(mime, size)
        if reason:
            return None, f"attachment[{i}] '{filename}': {reason}"

        # Text payloads are in memory here — verify they're actually UTF-8
        # so a wrongly-mime'd binary doesn't reach the agent. (Email runs the
        # same metadata check but skips this since payload is on disk.)
        if mime.startswith("text/") or mime == "application/json":
            try:
                payload.decode("utf-8")
            except UnicodeDecodeError:
                return None, f"attachment[{i}] '{filename}': not UTF-8 decodable"

        total_bytes += size
        if total_bytes > _MAX_TOTAL_BYTES:
            return None, "total attachment size exceeds 20 MB cap"

        decoded.append({
            "filename": filename,
            "mime_type": mime,
            "bytes": payload,
        })

    return decoded, None


def _unique_filename(existing: set[str], name: str) -> str:
    """Return `name`, suffixed `_1`, `_2`, ... if it collides with anything in `existing`."""
    if name not in existing:
        return name
    if "." in name:
        stem, _, ext = name.rpartition(".")
        ext = "." + ext
    else:
        stem, ext = name, ""
    i = 1
    while f"{stem}_{i}{ext}" in existing:
        i += 1
    return f"{stem}_{i}{ext}"


def _stage_attachments(items: list[dict], session_id: str, uploads_dir: str) -> list[dict]:
    """Write decoded items to disk, return an email-shaped manifest.

    Layout: <uploads_dir>/<session_id>/<uuid>/<normalized_filename>
    All items in one call share a single uuid subdir.

    ``session_id`` is validated and each item's filename is reduced to its
    basename before being joined, so a hostile portal/WS frame can't escape
    ``uploads_dir`` via ``..`` segments. The composed path is then realpath'd
    and checked against ``realpath(uploads_dir)`` to catch symlink-out
    attempts inside the staging tree.
    """
    if not items:
        return []

    _sanitize_session_id(session_id)

    batch_dir = os.path.join(uploads_dir, session_id, _uuid.uuid4().hex)
    os.makedirs(batch_dir, exist_ok=True)

    uploads_real = os.path.realpath(uploads_dir)

    manifest: list[dict] = []
    used: set[str] = set()
    for item in items:
        raw = _normalize_unicode_whitespace(item["filename"])
        # Drop any directory component the client supplied. `os.path.basename`
        # only handles forward slashes on POSIX, so also strip on backslash
        # for cross-platform safety.
        base = os.path.basename(raw.replace("\\", "/"))
        if not base or base in (".", ".."):
            raise ValueError(f"attachment filename rejected: {item['filename']!r}")
        fname = _unique_filename(used, base)
        used.add(fname)
        full_path = os.path.join(batch_dir, fname)
        full_real = os.path.realpath(full_path)
        if not (full_real == uploads_real or full_real.startswith(uploads_real + os.sep)):
            raise ValueError(
                f"attachment path escapes uploads_dir: {item['filename']!r}"
            )
        with open(full_path, "wb") as f:
            f.write(item["bytes"])
        manifest.append({
            "filename": fname,
            "path": full_path,
            "mime_type": item["mime_type"],
            "size": len(item["bytes"]),
        })
    return manifest
