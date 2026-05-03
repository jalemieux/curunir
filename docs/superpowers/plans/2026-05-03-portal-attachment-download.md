# Portal Attachment Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make agent-attached files clickable-to-download in the portal UI by base64-encoding bytes inside the existing `agent_message` payload.

**Architecture:** Two-file change. Backend: extend `_enrich_attachments` (`src/channels/_attachments.py`) to add a `data` field (base64) for attachments within mime-specific size caps, reusing the existing inbound caps. Frontend: extend `renderAttachments` (`portal/static/index.html`) to make chips clickable when `data` is present, triggering a Blob download. Portal service (`portal/ws_agent.py`) forwards the payload verbatim — no changes there.

**Tech Stack:** Python 3.12 (asyncio, pytest-asyncio), vanilla JS (no framework), WebSocket transport.

**Spec:** `docs/superpowers/specs/2026-05-03-portal-attachment-download-design.md`

---

## File Structure

**Modify:**
- `src/channels/_attachments.py` — add `_attach_download_data` helper; call it from `_enrich_attachments`.
- `portal/static/index.html` — extend `renderAttachments` JS function; add 2 CSS rules for `.attachment.downloadable`.
- `tests/test_attachment_enrichment.py` — add tests for the new `data` field.

**Do not touch:**
- `portal/ws_agent.py` — payload pass-through is already field-agnostic.
- `src/tools/attach.py` — tool output unchanged.
- `src/agent/agent.py` — attachment threading unchanged.

---

### Task 1: Backend — base64-encode downloadable attachment bytes

**Files:**
- Modify: `src/channels/_attachments.py`
- Test: `tests/test_attachment_enrichment.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_attachment_enrichment.py` (after the existing `test_enrich_normalizes_path` function, before the async test at the bottom):

```python
import base64 as _b64


def test_enrich_adds_data_for_image_under_cap():
    """Images under 5 MB get base64 `data` for download."""
    payload = b"\x89PNG\r\n" + b"\x00" * 1024
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir="/tmp") as f:
        f.write(payload)
        path = f.name
    try:
        attachments = [{"filename": "img.png", "path": path,
                        "mime_type": "image/png", "size": len(payload)}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert attachments[0]["data"] == _b64.b64encode(payload).decode("ascii")
    finally:
        os.unlink(path)


def test_enrich_adds_data_for_pdf_under_cap():
    """PDFs under 10 MB get base64 `data` for download."""
    payload = b"%PDF-1.4\n" + b"\x00" * 2048
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir="/tmp") as f:
        f.write(payload)
        path = f.name
    try:
        attachments = [{"filename": "doc.pdf", "path": path,
                        "mime_type": "application/pdf", "size": len(payload)}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert attachments[0]["data"] == _b64.b64encode(payload).decode("ascii")
    finally:
        os.unlink(path)


def test_enrich_adds_data_for_text_under_cap():
    """Text files ≤ 256 KB get both `content` (string) and `data` (base64)."""
    body = "hello world"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, dir="/tmp") as f:
        f.write(body)
        path = f.name
    try:
        attachments = [{"filename": "hello.md", "path": path,
                        "mime_type": "text/markdown", "size": len(body)}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert attachments[0]["content"] == body
        assert attachments[0]["data"] == _b64.b64encode(body.encode("utf-8")).decode("ascii")
    finally:
        os.unlink(path)


def test_enrich_omits_data_for_image_over_cap():
    """Images > 5 MB get no `data` field."""
    size = 5 * 1024 * 1024 + 1
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir="/tmp") as f:
        f.write(b"\x00" * size)
        path = f.name
    try:
        attachments = [{"filename": "big.png", "path": path,
                        "mime_type": "image/png", "size": size}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert "data" not in attachments[0]
    finally:
        os.unlink(path)


def test_enrich_omits_data_for_pdf_over_cap():
    """PDFs > 10 MB get no `data` field."""
    size = 10 * 1024 * 1024 + 1
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir="/tmp") as f:
        f.write(b"\x00" * size)
        path = f.name
    try:
        attachments = [{"filename": "big.pdf", "path": path,
                        "mime_type": "application/pdf", "size": size}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert "data" not in attachments[0]
    finally:
        os.unlink(path)


def test_enrich_omits_data_for_text_over_data_cap():
    """Text files > 256 KB get no `data` (even if under the 512 KB content cap)."""
    size = 256 * 1024 + 1
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, dir="/tmp") as f:
        f.write("x" * size)
        path = f.name
    try:
        attachments = [{"filename": "big.md", "path": path,
                        "mime_type": "text/markdown", "size": size}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert "data" not in attachments[0]
        # `content` is still populated since 256 KB+1 < 512 KB content cap.
        assert attachments[0]["content"] is not None
    finally:
        os.unlink(path)


def test_enrich_omits_data_for_unsupported_mime():
    """Mimes outside the allowed image/doc/text sets get no `data`."""
    payload = b"PK\x03\x04fakezip"
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False, dir="/tmp") as f:
        f.write(payload)
        path = f.name
    try:
        attachments = [{"filename": "thing.zip", "path": path,
                        "mime_type": "application/zip", "size": len(payload)}]
        _enrich_attachments(attachments, project_root="/tmp")
        assert "data" not in attachments[0]
    finally:
        os.unlink(path)


def test_enrich_missing_file_sets_error_no_data():
    """Missing file: `error` set, no `data` field, even for downloadable mime."""
    attachments = [{"filename": "gone.pdf", "path": "/tmp/nonexistent-xyz.pdf",
                    "mime_type": "application/pdf", "size": 100}]
    _enrich_attachments(attachments, project_root="/tmp")
    assert "data" not in attachments[0]
    assert attachments[0]["error"] == "file not found"


def test_enrich_is_idempotent_for_data():
    """Calling `_enrich_attachments` twice produces the same `data`."""
    payload = b"hello"
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir="/tmp") as f:
        f.write(payload)
        path = f.name
    try:
        attachments = [{"filename": "x.png", "path": path,
                        "mime_type": "image/png", "size": len(payload)}]
        _enrich_attachments(attachments, project_root="/tmp")
        first = attachments[0]["data"]
        # Second call: path is now relative; _enrich must still find the file.
        # Use absolute path again to simulate a fresh enrichment cycle.
        attachments[0]["path"] = path
        _enrich_attachments(attachments, project_root="/tmp")
        assert attachments[0]["data"] == first
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
pytest tests/test_attachment_enrichment.py -v -k "data"
```

Expected: 9 FAILED. `KeyError: 'data'` or `AssertionError: 'data' not in ...` on each.

- [ ] **Step 3: Implement `_attach_download_data` helper**

In `src/channels/_attachments.py`, add the helper function below the existing constants (right after the `_MAX_ATTACHMENT_CONTENT_SIZE = ...` line, before `def _enrich_attachments`):

```python
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
```

- [ ] **Step 4: Wire the helper into `_enrich_attachments`**

In `src/channels/_attachments.py`, modify `_enrich_attachments`. Find this block:

```python
        if os.path.isabs(path):
            try:
                att["path"] = os.path.relpath(path, project_root)
            except ValueError:
                pass  # different drive on Windows, keep absolute

        if not is_text:
            att["content"] = None
            continue
```

Insert one call between the path-rewrite block and the `if not is_text` block, so it reads:

```python
        if os.path.isabs(path):
            try:
                att["path"] = os.path.relpath(path, project_root)
            except ValueError:
                pass  # different drive on Windows, keep absolute

        _attach_download_data(att, path)

        if not is_text:
            att["content"] = None
            continue
```

`path` here is the local variable captured at the top of the loop iteration (the original, pre-rewrite value), so file I/O still works after the relpath rewrite.

- [ ] **Step 5: Run the new tests — verify pass**

```bash
pytest tests/test_attachment_enrichment.py -v -k "data"
```

Expected: 9 PASSED.

- [ ] **Step 6: Run the full test file — verify no regression in existing tests**

```bash
pytest tests/test_attachment_enrichment.py -v
```

Expected: ALL PASSED (9 new + 7 existing = 16 total).

- [ ] **Step 7: Run the full test suite — verify no other regression**

```bash
pytest tests/
```

Expected: ALL PASSED.

- [ ] **Step 8: Commit**

```bash
git add src/channels/_attachments.py tests/test_attachment_enrichment.py
git commit -m "feat(portal): inline base64 bytes for downloadable attachments"
```

---

### Task 2: Frontend — clickable download chips in portal UI

**Files:**
- Modify: `portal/static/index.html` (CSS block + JS `renderAttachments`)

No automated tests — the portal has no JS test harness. Manual smoke test in Task 3.

- [ ] **Step 1: Add CSS for downloadable chip state**

In `portal/static/index.html`, find the existing `.attachment` CSS rule (around line 63-68). Immediately after it, add two new rules. The block should read:

```css
.attachment {
  display: inline-flex; align-items: center; gap: 4px;
  background: #1a1a2e; border: 1px solid #2a2a3e; border-radius: 4px;
  padding: 3px 8px; font-size: 11px; color: var(--accent);
  margin-right: 4px; margin-top: 4px;
}
.attachment.downloadable { cursor: pointer; }
.attachment.downloadable:hover { color: #fff; border-color: var(--accent); }
```

Do NOT modify the `.staged-list .attachment` rules — those remain red-on-hover for "remove" semantics in the composer.

- [ ] **Step 2: Extend `renderAttachments` to make chips clickable when `data` is present**

In `portal/static/index.html`, find the existing function (around line 247-257):

```javascript
function renderAttachments(parent, atts) {
  if (!atts || !atts.length) return;
  const wrap = document.createElement("div");
  for (const a of atts) {
    const chip = document.createElement("span");
    chip.className = "attachment";
    chip.textContent = `📄 ${a.filename || a.path || "file"}`;
    wrap.appendChild(chip);
  }
  parent.appendChild(wrap);
}
```

Replace it with:

```javascript
function renderAttachments(parent, atts) {
  if (!atts || !atts.length) return;
  const wrap = document.createElement("div");
  for (const a of atts) {
    const chip = document.createElement("span");
    chip.className = "attachment";
    chip.textContent = `📄 ${a.filename || a.path || "file"}`;
    if (typeof a.data === "string") {
      chip.classList.add("downloadable");
      chip.title = "Download";
      chip.onclick = () => downloadAttachment(a);
    }
    wrap.appendChild(chip);
  }
  parent.appendChild(wrap);
}

function downloadAttachment(a) {
  const bin = atob(a.data);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const blob = new Blob([bytes], { type: a.mime_type || "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = a.filename || "download";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
```

- [ ] **Step 3: Verify no syntax errors via lint/parse**

There is no JS bundler in this project. Quick parse check:

```bash
node --check portal/static/index.html 2>&1 || true
```

(That will error because `index.html` is HTML, not JS — that's expected.) Instead, extract and check just the script tag:

```bash
python3 -c "
import re, pathlib
html = pathlib.Path('portal/static/index.html').read_text()
m = re.search(r'<script>(.*?)</script>', html, re.DOTALL)
assert m, 'no <script> block'
pathlib.Path('/tmp/portal_check.js').write_text(m.group(1))
" && node --check /tmp/portal_check.js
```

Expected: no output (parse succeeded).

- [ ] **Step 4: Commit**

```bash
git add portal/static/index.html
git commit -m "feat(portal): clickable download for agent-attached files"
```

---

### Task 3: Manual smoke test

**Files:** none modified. Verification only.

This is the only end-to-end check that the wire format stays compatible (container → portal → browser) and that the click actually downloads a usable file.

- [ ] **Step 1: Start the local stack**

In one terminal, run the agent:

```bash
source .venv/bin/activate && python run.py
```

In another terminal, run the portal locally (see `portal/README.md` for the full command; usually):

```bash
cd portal && uvicorn app:app --reload --port 8080
```

Set `CURUNIR_PORTAL_URL=ws://localhost:8080/ws/agent` and `CURUNIR_PORTAL_TOKEN=<dev token>` in the agent env so it dials the local portal.

- [ ] **Step 2: Test PDF download**

In the browser at `http://localhost:8080`, send the agent: `attach the file at <path-to-any-small-PDF-on-disk>`.

Wait for the agent reply with the `📄 filename.pdf` chip. Hover — cursor should be a pointer, color should brighten. Click the chip.

Expected: browser downloads `filename.pdf`. Open it; it should be the same PDF.

- [ ] **Step 3: Test image download**

Repeat with a small PNG. Expected: chip is clickable, downloaded image opens correctly.

- [ ] **Step 4: Test text download**

Repeat with a small `.txt` or `.md` file. Expected: chip is clickable, downloaded file matches original bytes.

- [ ] **Step 5: Test oversized file is non-clickable**

Create an 11 MB PDF: `dd if=/dev/urandom of=/tmp/big.pdf bs=1M count=11`. Ask the agent to attach it.

Expected: chip renders (`📄 big.pdf`) but cursor stays default on hover, no `title="Download"` tooltip, click does nothing.

- [ ] **Step 6: Test unsupported mime is non-clickable**

Create a small zip: `echo hi | zip /tmp/x.zip -`. Ask the agent to attach `/tmp/x.zip`.

Expected: chip renders but is not clickable.

- [ ] **Step 7: Confirm and finalize**

If all six smoke checks pass, the feature is done. No commit needed (no files changed in this task).

If any check fails, capture the browser console output and the agent log line for that turn, then debug from there.

---

## Self-Review Notes

- **Spec coverage:** Backend caps (5/10/0.256 MB) → Task 1 tests 4-6. Frontend click + Blob download → Task 2. Portal pass-through → not modified, called out as such. History-snapshot non-coverage → explicit non-goal in spec, not in plan. ✓
- **Type/name consistency:** Helper named `_attach_download_data` in both definition and call site. JS function `downloadAttachment` defined and called consistently. CSS class `downloadable` matches in both CSS rule and JS `classList.add`. ✓
- **No placeholders:** every code step contains the actual code; every command has expected output. ✓
