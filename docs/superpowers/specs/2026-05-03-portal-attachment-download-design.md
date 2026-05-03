# Portal Attachment Download

**Date:** 2026-05-03
**Scope:** `src/channels/_attachments.py` (backend enrichment) and `portal/static/index.html` (UI click handler). No portal-service changes.

## Problem

When the agent calls the `attach` tool inside a portal session, the attachment surfaces in the browser as a non-interactive `📄 filename` chip. The bytes never travel — `_enrich_attachments` only inlines `text/*` and `application/json` (as a `content` string the UI ignores), and PDFs/images/binaries get `content=None`. Result: the user can see *that* the agent attached a file but cannot retrieve it without shell access to the container.

Concretely, in the screenshot that motivated this spec, the agent generated a PDF report (`Family Vacation Destinations - June-July 2026.pdf`) and there was no way to download it from the UI.

## Goals

- Click an attachment chip → file downloads with its original filename.
- Works for the file types the inbound flow already validates: PNG/JPEG/GIF/WebP images, PDFs, and UTF-8 text/JSON.
- Symmetric size caps with inbound uploads (no surprise that the agent can send what the user can upload, but no more).
- No new endpoints, no new dependencies, no portal-service changes.

## Non-Goals

- Reload-restore: `Agent.history_snapshot` (`src/agent/agent.py:160`) already strips attachments from past assistant turns. Reloading the portal will not surface old downloadable files. That gap predates this work and is left as-is.
- Inline preview/embedding (PDF viewer, image thumbnail, syntax-highlighted text). The existing `content` field is left in place but the UI continues to ignore it; preview is a separate design.
- Streaming or chunked transfer. Caps keep payloads inside one WebSocket frame.
- Arbitrary binary mime types (e.g. `.zip`, `.docx`). If the agent attaches one, the chip renders as today (no `data`, not clickable). Adding new types is a one-line cap addition later.

## Design

### Backend: `_enrich_attachments`

Today this function (in `src/channels/_attachments.py`) inlines text content and rewrites paths. Extend it to also populate a new field, `data`, on each attachment when the file is within download caps:

| Mime category | Allowed mimes | Cap |
|---|---|---|
| Image | `image/png`, `image/jpeg`, `image/gif`, `image/webp` | 5 MB |
| Document | `application/pdf` | 10 MB |
| Text | `text/*`, `application/json` | 256 KB |

These are the same caps and mime sets as `_decode_attachments` (the inbound path), already defined as module-level constants (`_ALLOWED_IMAGE_MIMES`, `_ALLOWED_DOC_MIMES`, `_MAX_IMAGE_BYTES`, `_MAX_DOC_BYTES`, `_MAX_TEXT_BYTES`). Reuse them — do not duplicate.

Rules:

- File missing → `error="file not found"`, no `data`. (Existing behavior for the `content` branch; extend to all branches.)
- File over the per-type cap → no `data` (chip will render but not be clickable).
- Mime not in the allowed set → no `data`.
- Otherwise → `data = base64.b64encode(open(path,"rb").read()).decode("ascii")`.

The existing `content` field is unchanged: text/JSON files ≤ 512 KB still get `content=<utf-8 string>`. `content` and `data` are independent — `content` is for inline rendering (currently unused by the UI), `data` is for download. The 512 KB `content` cap is the existing behavior; the 256 KB `data` cap matches the inbound limit. They differ on purpose; do not unify.

No path normalization changes. The relative-path rewrite stays.

### Frontend: `renderAttachments`

In `portal/static/index.html`, the `renderAttachments(parent, atts)` function builds a `<span class="attachment">` chip per attachment. Extend it:

- If `a.data` is present (a string, possibly empty for a 0-byte file), attach an `onclick` that:
  1. Decodes `a.data` (base64 → `Uint8Array`).
  2. Constructs `new Blob([bytes], {type: a.mime_type})`.
  3. Creates a hidden `<a>` with `href=URL.createObjectURL(blob)`, `download=a.filename`, clicks it, then `URL.revokeObjectURL(href)`.
- Add `cursor: pointer` and a hover color shift (reuse the existing `.staged-list .attachment:hover` pattern but in the accent/blue color, not red — red means "remove" in the staged list).
- Set `title="Download"` on clickable chips.
- If `a.data` is absent/empty, the chip renders exactly as today (label-only, no cursor change, no tooltip).

No CSS framework changes; one new selector for the hover state.

### Portal service

No changes. `portal/ws_agent.py:65` forwards the `agent_message` payload verbatim to browsers via `routing.fan_out_to_browsers`. The new `data` field rides along automatically.

## Data Flow

```
Agent.attach(path)                            (src/tools/attach.py)
  → attachments list = [{filename, path, mime_type, size}]
  → returned as part of OutgoingMessage
PortalChannel.send(msg)                       (src/channels/portal.py:158)
  → _enrich_attachments(msg.attachments, cwd) (src/channels/_attachments.py)
      → adds `data` (base64) when within caps
      → adds `content` (text) for small text/json (existing)
      → adds `error` if file missing (existing)
  → JSON-wrap as agent_message, send over WS
Portal ws_agent.py
  → forwards payload verbatim to browsers
Browser renderAttachments()                   (portal/static/index.html:247)
  → if data: chip is clickable → blob download
  → else: chip is a label (existing)
```

## Error Handling

- **File missing on disk between attach and send:** `error="file not found"` is set; chip renders as label. No download offered.
- **File too large:** No `data`. UI is the same as the missing-file case (label-only chip). The agent's tool result already told the model the file was attached; the user simply can't pull it down. Acceptable for v1; an explicit "too large" UI affordance can come later if it becomes a real complaint.
- **Base64 decode fails in the browser:** Should not happen since we generated the string. If it does, browser logs and the click silently no-ops. No user-facing error toast.
- **WebSocket frame > 32 MB cap (`max_size` in `PortalChannel.start`):** Cannot happen given the per-attachment caps (≤ 10 MB) and the inbound 20 MB total cap on user uploads. The 32 MB frame ceiling has ~12 MB of headroom for JSON envelope + multiple attachments.

## Testing

**Unit tests** in `tests/test_attachment_enrichment.py` (already exists, covers `_enrich_attachments`):

- `_enrich_attachments` adds `data` for an image under cap.
- `_enrich_attachments` adds `data` for a PDF under cap.
- `_enrich_attachments` adds `data` for a text file under 256 KB.
- `_enrich_attachments` omits `data` for an image over 5 MB.
- `_enrich_attachments` omits `data` for a PDF over 10 MB.
- `_enrich_attachments` omits `data` for a text file over 256 KB (but `content` already absent at that size — verify both).
- `_enrich_attachments` omits `data` for an unsupported mime (e.g. `application/zip`).
- `_enrich_attachments` sets `error` and omits `data` when the file does not exist.
- `_enrich_attachments` is idempotent (calling twice produces the same dict shape; the second call shouldn't double-encode).

**Manual smoke test:**

1. From the portal UI, ask the agent to write a small PDF and attach it.
2. Verify a `📄 filename` chip appears in the agent's reply.
3. Click it. File downloads with the correct filename and opens correctly.
4. Repeat with: a small text file, a PNG, and a > 10 MB PDF (chip should render but not be clickable).

## Files Touched

- `src/channels/_attachments.py` — extend `_enrich_attachments` with the `data` field; reuse existing mime/cap constants.
- `portal/static/index.html` — extend `renderAttachments` with click handler + minor CSS for hover/cursor.
- `tests/test_attachment_enrichment.py` — unit tests per above.
