"""WebSocket-flavored attachment helpers, shared between channels.

Extracted from src/channels/ws.py so the new PortalChannel can reuse
the exact same validation, decoding, staging, filename normalization,
and outbound enrichment without duplication.
"""

import base64
import os
import re
import uuid as _uuid
from pathlib import Path

# Regex matching any Unicode whitespace character that isn't a regular space.
_UNICODE_WHITESPACE_RE = re.compile(r'[^\S ]+')


def _normalize_unicode_whitespace(s: str) -> str:
    """Replace Unicode whitespace characters (e.g. \\u202f) with regular spaces.

    LLMs convert exotic whitespace to regular spaces when generating tool
    calls, so attachment filenames need normalizing on intake to avoid
    file-not-found errors downstream.
    """
    return _UNICODE_WHITESPACE_RE.sub(' ', s)


# Windows reserved device names — rejected even on Linux since the staging
# dir may later be synced to a Windows host or shared via SMB.
_WIN_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})


def _safe_attachment_filename(raw: str) -> str | None:
    """Normalize a remote-supplied filename for safe use as a basename.

    Returns the sanitized name, or ``None`` if the input cannot be made
    safe and the attachment should be skipped. Strips path components
    (POSIX and Windows separators), rejects empty / dot / NUL-bearing
    names, drops leading dots so silent dotfile creation is impossible,
    and rejects Windows reserved device names.
    """
    if not isinstance(raw, str):
        return None
    s = _normalize_unicode_whitespace(raw)
    if "\x00" in s:
        return None
    # Strip directory components for both POSIX and Windows separators —
    # a Windows-style filename can arrive on a Linux host and vice versa.
    for sep in ("/", "\\"):
        if sep in s:
            s = s.rsplit(sep, 1)[-1]
    s = s.strip()
    if not s or s in (".", ".."):
        return None
    s = s.lstrip(".")
    if not s:
        return None
    stem = s.split(".", 1)[0].upper()
    if stem in _WIN_RESERVED:
        return None
    return s


def _assert_within(parent: Path, child: Path) -> bool:
    """Return True iff ``child`` resolves to a path inside ``parent``.

    Defense-in-depth against symlinks under the staging dir: even after
    basenaming, a symlink at ``parent/foo`` could redirect a write outside
    ``parent``. ``resolve()`` follows symlinks; we then confirm containment.
    """
    try:
        child_r = child.resolve()
        parent_r = parent.resolve()
    except OSError:
        return False
    try:
        child_r.relative_to(parent_r)
    except ValueError:
        return False
    return True

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
    """
    if not items:
        return []

    batch_dir = os.path.join(uploads_dir, session_id, _uuid.uuid4().hex)
    os.makedirs(batch_dir, exist_ok=True)

    manifest: list[dict] = []
    used: set[str] = set()
    batch_dir_path = Path(batch_dir)
    for item in items:
        safe = _safe_attachment_filename(item["filename"])
        if safe is None:
            continue
        fname = _unique_filename(used, safe)
        full_path = batch_dir_path / fname
        if not _assert_within(batch_dir_path, full_path):
            continue
        used.add(fname)
        with open(full_path, "wb") as f:
            f.write(item["bytes"])
        manifest.append({
            "filename": fname,
            "path": str(full_path),
            "mime_type": item["mime_type"],
            "size": len(item["bytes"]),
        })
    return manifest
