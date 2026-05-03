"""WebSocket-flavored attachment helpers, shared between channels.

Extracted from src/channels/ws.py so the new PortalChannel can reuse
the exact same validation, decoding, staging, filename normalization,
and outbound enrichment without duplication.
"""

import base64
import os
import uuid as _uuid

from src.channels.email import _normalize_unicode_whitespace

# Size caps (mirrored in cli.py)
_MAX_IMAGE_BYTES = 5 * 1024 * 1024          # 5 MB
_MAX_TEXT_BYTES = 256 * 1024                # 256 KB
_MAX_DOC_BYTES = 10 * 1024 * 1024           # 10 MB (PDFs)
_MAX_TOTAL_BYTES = 20 * 1024 * 1024         # 20 MB
_ALLOWED_IMAGE_MIMES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
})
_ALLOWED_DOC_MIMES = frozenset({"application/pdf"})

_MAX_ATTACHMENT_CONTENT_SIZE = 512 * 1024  # 512KB


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

        if mime.startswith("image/"):
            if mime not in _ALLOWED_IMAGE_MIMES:
                return None, (
                    f"attachment[{i}] '{filename}': "
                    f"unsupported image type {mime}"
                )
            if size > _MAX_IMAGE_BYTES:
                return None, (
                    f"attachment[{i}] '{filename}': "
                    f"{size} bytes exceeds 5 MB image cap"
                )
        elif mime in _ALLOWED_DOC_MIMES:
            if size > _MAX_DOC_BYTES:
                return None, (
                    f"attachment[{i}] '{filename}': "
                    f"{size} bytes exceeds 10 MB document cap"
                )
        else:
            if size > _MAX_TEXT_BYTES:
                return None, (
                    f"attachment[{i}] '{filename}': "
                    f"{size} bytes exceeds 256 KB text cap"
                )
            try:
                payload.decode("utf-8")
            except UnicodeDecodeError:
                return None, (
                    f"attachment[{i}] '{filename}': "
                    f"not UTF-8 decodable"
                )

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
    for item in items:
        fname = _unique_filename(used, _normalize_unicode_whitespace(item["filename"]))
        used.add(fname)
        full_path = os.path.join(batch_dir, fname)
        with open(full_path, "wb") as f:
            f.write(item["bytes"])
        manifest.append({
            "filename": fname,
            "path": full_path,
            "mime_type": item["mime_type"],
            "size": len(item["bytes"]),
        })
    return manifest
