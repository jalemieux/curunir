# CLI File Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let CLI users attach images and UTF-8 text files to a message so the agent sees them as native multimodal content, bringing CLI inbound parity with the email channel.

**Architecture:** CLI `/attach` stages files client-side, base64-encodes them in the outbound JSON, server-side `ws.py` validates/decodes/writes them to `context/uploads/<session_id>/<uuid>/`, producing an email-shaped `IncomingMessage.attachments` manifest. `run.py` branches for `cli` channel to build LiteLLM-compatible multimodal content blocks via a new `build_multimodal_content` helper. `Agent.handle()` already accepts `str | list` — we just need history/trim support for list content.

**Tech Stack:** Python 3.12+, asyncio, `websockets`, `prompt_toolkit` (CLI), `rich`, `litellm`, `pytest-asyncio`.

**Spec reference:** `docs/superpowers/specs/2026-04-23-cli-file-upload-design.md`

---

## File Structure

| File | Role |
|---|---|
| `src/channels/ws.py` | Size/MIME constants; validate inbound `attachments`; decode base64; write to uploads dir; build email-shaped manifest; reply with error `OutgoingMessage` on failure. |
| `src/channels/base.py` | Docstring note on the attachment dict schema — no structural change. |
| `run.py` | New `build_multimodal_content(text, attachments)` helper. Branch in `agent_worker` so `cli` channel uses it. Purge `context/uploads/<session_id>/` on `clear`/`reset` commands. |
| `src/agent/agent.py` | Extend `_estimate_chars` to charge a fixed cost per image block; leave `Agent.handle()`'s existing `str | list` typing untouched (already supports list content in history). |
| `cli.py` | New `/attach`, `/detach`, `/attach clear` commands. Staging list. Client-side validation. Staging cleared on send and on session-reset commands. |
| `tests/conftest.py` | New `tmp_uploads` fixture used by ws + integration tests. |
| `tests/test_ws_channel.py` | Extend with validation + staging + error-reply tests. |
| `tests/test_build_content.py` | **New** — unit-tests `build_multimodal_content` (empty, image-only, text-only, mixed, empty prompt + image). |
| `tests/test_agent.py` | Extend: `_trim_history` charges per-image cost; `Agent.handle(message=list)` stores list as-is in history and passes it to `call_llm`. |
| `tests/test_cli_client.py` | Extend: `/attach`, `/detach`, `/attach clear`, batch send, staging-cleared-after-send, `/clear` purges staging, client-side validation. |

All constants (size caps, allowed MIMEs) live in `ws.py` and are **mirrored** in `cli.py`. Promotion to env vars is deliberately out of scope.

---

## Task 1: Agent supports multimodal history

**Files:**
- Modify: `src/agent/agent.py:70-80` (`_estimate_chars`)
- Test: `tests/test_agent.py` (extend)

Agent.handle() already types `message` as `str | list` and appends it to history unchanged. The only gap is that `_estimate_chars` currently does `len(str(block))` for list content, which would serialize a base64 image URL and produce a massive (and slightly nonsensical) char count. We replace it with a rule: text blocks cost their text length; image blocks cost a fixed `_IMAGE_COST_CHARS`.

- [ ] **Step 1.1: Write the failing test for `_trim_history` image cost**

Append to `tests/test_agent.py` (inside a new class `TestTrimHistoryMultimodal`):

```python
from src.agent.agent import _trim_history, _estimate_chars


class TestTrimHistoryMultimodal:
    def test_image_block_costs_fixed_amount(self):
        # A single image block with a large data-URI should cost
        # the fixed per-image charge (2000), not the actual URL length.
        big_url = "data:image/png;base64," + ("A" * 500_000)
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": big_url}},
            ],
        }
        # text "hi" = 2 chars, image = 2000 chars, total = 2002
        assert _estimate_chars([msg]) == 2 + 2000

    def test_trim_keeps_recent_multimodal_messages(self):
        big_url = "data:image/png;base64," + ("A" * 10)
        history = [
            {"role": "user", "content": "old message " * 1000},        # ~12k chars
            {"role": "assistant", "content": "old reply " * 1000},      # ~10k chars
            {"role": "user", "content": [
                {"type": "text", "text": "recent"},
                {"type": "image_url", "image_url": {"url": big_url}},
            ]},
            {"role": "assistant", "content": "recent reply"},
        ]
        _trim_history(history, max_chars=5_000)
        # Oldest user+assistant pair should be dropped; the multimodal
        # message must survive.
        assert len(history) == 2
        assert history[0]["content"][0]["text"] == "recent"
```

- [ ] **Step 1.2: Run the test to verify it fails**

Run: `pytest tests/test_agent.py::TestTrimHistoryMultimodal -v`
Expected: FAIL — `test_image_block_costs_fixed_amount` reports a char count dominated by the base64 URL (> 500k), not 2002.

- [ ] **Step 1.3: Update `_estimate_chars` in `src/agent/agent.py`**

Replace the existing body (lines 70-80):

```python
_IMAGE_COST_CHARS = 2000  # fixed budget per image block for history trimming


def _estimate_chars(messages: list[dict]) -> int:
    """Rough character count across all message contents.

    For list-form content (multimodal messages), text blocks count their
    text length and image blocks charge a fixed per-image cost so images
    age out of history alongside text on long sessions.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    total += len(str(block))
                    continue
                btype = block.get("type")
                if btype == "text":
                    total += len(block.get("text", ""))
                elif btype == "image_url":
                    total += _IMAGE_COST_CHARS
                else:
                    total += len(str(block))
    return total
```

- [ ] **Step 1.4: Run the tests to verify they pass**

Run: `pytest tests/test_agent.py::TestTrimHistoryMultimodal -v`
Expected: PASS (2 tests).

- [ ] **Step 1.5: Add a test that `Agent.handle(message=list)` threads list content all the way through**

Append to `tests/test_agent.py` inside the existing `TestAgentHandle` class:

```python
    async def test_accepts_list_content_and_forwards_to_llm(self, agent):
        captured: dict = {}

        async def fake_call_llm(model, messages, tools, **kwargs):
            # Grab the user message the LLM was called with.
            captured["messages"] = messages
            from src.llm import LLMResponse
            return LLMResponse(text="ack", tool_calls=None)

        content_blocks = [
            {"type": "text", "text": "describe this"},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]

        with patch("src.agent.agent.call_llm", new=fake_call_llm):
            result = await agent.handle(content_blocks, "s1")

        assert result == "ack"
        # History stores the list verbatim.
        assert agent.sessions["s1"][0]["content"] == content_blocks
        # call_llm received the same list (not stringified).
        user_msg = [m for m in captured["messages"] if m["role"] == "user"][-1]
        assert user_msg["content"] == content_blocks
```

- [ ] **Step 1.6: Run the new test**

Run: `pytest tests/test_agent.py::TestAgentHandle::test_accepts_list_content_and_forwards_to_llm -v`
Expected: PASS (Agent.handle already appends message to history as-is; no code change needed).

- [ ] **Step 1.7: Run the full agent test file to guard against regressions**

Run: `pytest tests/test_agent.py -v`
Expected: all green.

- [ ] **Step 1.8: Commit**

```bash
git add src/agent/agent.py tests/test_agent.py
git commit -m "feat(agent): charge fixed cost per image block in history trim"
```

---

## Task 2: WebSocket channel — inbound attachment validation (pure helpers)

**Files:**
- Modify: `src/channels/ws.py` (add constants + pure validator function)
- Test: `tests/test_ws_channel.py` (extend)

Introduce constants and a pure `_decode_attachments(payload_attachments)` helper that returns either `(list[decoded_item], None)` or `(None, error_str)`. No disk I/O here — just base64 decode, type checks, size/MIME checks, UTF-8 sniff for non-image items.

A decoded item is a small dict `{"filename": str, "mime_type": str, "bytes": bytes}` — a pre-staging record.

- [ ] **Step 2.1: Write failing validator tests**

Append to `tests/test_ws_channel.py`:

```python
import base64

from src.channels.ws import _decode_attachments


class TestDecodeAttachments:
    def test_none_or_empty_returns_empty_list(self):
        assert _decode_attachments(None) == ([], None)
        assert _decode_attachments([]) == ([], None)

    def test_valid_image_and_text(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        items, err = _decode_attachments([
            {"filename": "a.png", "mime_type": "image/png",
             "data": base64.b64encode(png).decode()},
            {"filename": "notes.txt", "mime_type": "text/plain",
             "data": base64.b64encode(b"hello world").decode()},
        ])
        assert err is None
        assert len(items) == 2
        assert items[0]["bytes"] == png
        assert items[1]["bytes"] == b"hello world"

    def test_missing_field_rejected(self):
        items, err = _decode_attachments([
            {"filename": "a.png", "mime_type": "image/png"},  # no data
        ])
        assert items is None
        assert "data" in err

    def test_bad_base64_rejected(self):
        items, err = _decode_attachments([
            {"filename": "a.png", "mime_type": "image/png", "data": "!!!"},
        ])
        assert items is None
        assert "base64" in err.lower()

    def test_oversized_image_rejected(self):
        big = b"\x00" * (5 * 1024 * 1024 + 1)
        items, err = _decode_attachments([
            {"filename": "big.png", "mime_type": "image/png",
             "data": base64.b64encode(big).decode()},
        ])
        assert items is None
        assert "5" in err and "MB" in err

    def test_oversized_text_rejected(self):
        big = b"x" * (256 * 1024 + 1)
        items, err = _decode_attachments([
            {"filename": "big.txt", "mime_type": "text/plain",
             "data": base64.b64encode(big).decode()},
        ])
        assert items is None
        assert "256" in err and "KB" in err

    def test_total_batch_size_cap(self):
        # Two 4 MB images + one 13 MB image = 21 MB total, over 20 MB cap.
        four_mb = b"\x00" * (4 * 1024 * 1024)
        thirteen_mb = b"\x00" * (13 * 1024 * 1024)
        items, err = _decode_attachments([
            {"filename": f"a{i}.png", "mime_type": "image/png",
             "data": base64.b64encode(four_mb).decode()} for i in range(2)
        ] + [
            {"filename": "b.png", "mime_type": "image/png",
             "data": base64.b64encode(thirteen_mb).decode()},
        ])
        assert items is None
        assert "20" in err and "MB" in err

    def test_disallowed_image_mime_rejected(self):
        items, err = _decode_attachments([
            {"filename": "a.bmp", "mime_type": "image/bmp",
             "data": base64.b64encode(b"bmpdata").decode()},
        ])
        assert items is None
        assert "image/bmp" in err

    def test_non_image_must_be_utf8(self):
        items, err = _decode_attachments([
            {"filename": "binary.bin", "mime_type": "application/octet-stream",
             "data": base64.b64encode(b"\xff\xfe\xfa").decode()},
        ])
        assert items is None
        assert "UTF-8" in err

    def test_attachments_must_be_list(self):
        items, err = _decode_attachments({"filename": "a.png"})
        assert items is None
        assert "list" in err.lower()
```

- [ ] **Step 2.2: Run the validator tests and confirm they fail**

Run: `pytest tests/test_ws_channel.py::TestDecodeAttachments -v`
Expected: FAIL — `_decode_attachments` doesn't exist yet.

- [ ] **Step 2.3: Implement constants and `_decode_attachments` in `src/channels/ws.py`**

Add at module top, alongside the existing constants:

```python
import base64

# Size caps (mirrored in cli.py)
_MAX_IMAGE_BYTES = 5 * 1024 * 1024          # 5 MB
_MAX_TEXT_BYTES = 256 * 1024                # 256 KB
_MAX_TOTAL_BYTES = 20 * 1024 * 1024         # 20 MB
_ALLOWED_IMAGE_MIMES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
})


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
            return None, f"total attachment size exceeds 20 MB cap"

        decoded.append({
            "filename": filename,
            "mime_type": mime,
            "bytes": payload,
        })

    return decoded, None
```

- [ ] **Step 2.4: Run the validator tests and confirm they pass**

Run: `pytest tests/test_ws_channel.py::TestDecodeAttachments -v`
Expected: PASS (9 tests).

- [ ] **Step 2.5: Commit**

```bash
git add src/channels/ws.py tests/test_ws_channel.py
git commit -m "feat(ws): validate and decode inbound attachment payloads"
```

---

## Task 3: WebSocket channel — staging decoded items to disk

**Files:**
- Modify: `src/channels/ws.py` (`_stage_attachments` helper + `uploads_dir` constructor param)
- Test: `tests/test_ws_channel.py`, `tests/conftest.py` (new `tmp_uploads` fixture)

Staged layout: `<uploads_dir>/<session_id>/<uuid>/<normalized_filename>`. Uuid is generated **once per batch** so all files in one message land together. Filenames are normalized for Unicode whitespace (reusing the helper from `email.py`). Collisions within a batch are suffixed `_1`, `_2`, etc. The returned manifest matches the email channel's shape exactly: `{filename, path, mime_type, size}`.

- [ ] **Step 3.1: Add `tmp_uploads` fixture to `tests/conftest.py`**

Append:

```python
@pytest.fixture
def tmp_uploads(tmp_path):
    """Temporary uploads root for WebSocket channel tests."""
    d = tmp_path / "uploads"
    d.mkdir()
    return d
```

- [ ] **Step 3.2: Write failing staging tests**

Append to `tests/test_ws_channel.py`:

```python
import os

from src.channels.ws import _stage_attachments


class TestStageAttachments:
    def test_writes_files_under_session_uuid(self, tmp_uploads):
        items = [
            {"filename": "a.png", "mime_type": "image/png", "bytes": b"PNG"},
            {"filename": "notes.txt", "mime_type": "text/plain", "bytes": b"hi"},
        ]
        manifest = _stage_attachments(items, "sid1", str(tmp_uploads))
        assert len(manifest) == 2
        # Both files share one uuid subdirectory.
        uuids = {os.path.basename(os.path.dirname(m["path"])) for m in manifest}
        assert len(uuids) == 1
        # Shape matches email channel.
        assert set(manifest[0].keys()) == {"filename", "path", "mime_type", "size"}
        # Bytes landed on disk.
        for m, src in zip(manifest, items):
            with open(m["path"], "rb") as f:
                assert f.read() == src["bytes"]
            assert m["size"] == len(src["bytes"])

    def test_normalizes_unicode_whitespace_in_filename(self, tmp_uploads):
        # U+202F (narrow no-break space) in filename should become a regular space.
        items = [{"filename": "weird name.txt",
                  "mime_type": "text/plain", "bytes": b"x"}]
        manifest = _stage_attachments(items, "sid", str(tmp_uploads))
        assert manifest[0]["filename"] == "weird name.txt"
        assert os.path.isfile(manifest[0]["path"])

    def test_collision_suffix(self, tmp_uploads):
        items = [
            {"filename": "same.txt", "mime_type": "text/plain", "bytes": b"1"},
            {"filename": "same.txt", "mime_type": "text/plain", "bytes": b"2"},
            {"filename": "same.txt", "mime_type": "text/plain", "bytes": b"3"},
        ]
        manifest = _stage_attachments(items, "sid", str(tmp_uploads))
        names = [m["filename"] for m in manifest]
        assert names == ["same.txt", "same_1.txt", "same_2.txt"]
        # Each file keeps its own bytes.
        for m, src in zip(manifest, items):
            with open(m["path"], "rb") as f:
                assert f.read() == src["bytes"]

    def test_empty_items_returns_empty_manifest(self, tmp_uploads):
        assert _stage_attachments([], "sid", str(tmp_uploads)) == []
```

- [ ] **Step 3.3: Run the staging tests and confirm they fail**

Run: `pytest tests/test_ws_channel.py::TestStageAttachments -v`
Expected: FAIL — `_stage_attachments` doesn't exist.

- [ ] **Step 3.4: Implement `_stage_attachments` in `src/channels/ws.py`**

Add imports and the helper:

```python
import os
import uuid as _uuid

from src.channels.email import _normalize_unicode_whitespace


def _unique_filename(existing: set[str], name: str) -> str:
    """Return `name`, suffixed `_1`, `_2`, ... if it collides with anything in `existing`."""
    if name not in existing:
        return name
    stem, _, ext = name.rpartition(".")
    if not stem:  # no dot in name
        stem, ext = name, ""
    else:
        ext = "." + ext
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
```

- [ ] **Step 3.5: Run the staging tests and confirm they pass**

Run: `pytest tests/test_ws_channel.py::TestStageAttachments -v`
Expected: PASS (4 tests).

- [ ] **Step 3.6: Commit**

```bash
git add src/channels/ws.py tests/test_ws_channel.py tests/conftest.py
git commit -m "feat(ws): stage decoded attachments to uploads/<session>/<uuid>/"
```

---

## Task 4: Wire validation + staging into `WebSocketChannel._handle_connection`

**Files:**
- Modify: `src/channels/ws.py` (`WebSocketChannel.__init__` and `_handle_connection`)
- Modify: `src/channels/base.py` (docstring note)
- Test: `tests/test_ws_channel.py`

Accept `uploads_dir` in the channel constructor (default `<cwd>/context/uploads`). Parse `attachments` from inbound JSON, validate, stage, attach the manifest to `IncomingMessage`. On any validation failure, drop the message entirely and push a user-facing error `OutgoingMessage` through `self.send(...)` (direct to the current connection — `send` doesn't need to round-trip through the router for this).

- [ ] **Step 4.1: Write failing end-to-end ws tests**

Append to `tests/test_ws_channel.py`:

```python
class TestWsAttachmentsE2E:
    @pytest.mark.asyncio
    async def test_valid_batch_produces_incoming_message_with_manifest(self, tmp_uploads):
        q = asyncio.Queue()
        ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 20,
                              uploads_dir=str(tmp_uploads))
        task = await _start_channel(ch)
        try:
            async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 20}") as ws:
                await ws.send(json.dumps({
                    "content": "compare these",
                    "command": None,
                    "attachments": [
                        {"filename": "a.png", "mime_type": "image/png",
                         "data": base64.b64encode(b"PNGDATA").decode()},
                        {"filename": "b.txt", "mime_type": "text/plain",
                         "data": base64.b64encode(b"hello").decode()},
                    ],
                }))
                await asyncio.sleep(0.1)

            msg = q.get_nowait()
            assert msg.content == "compare these"
            assert msg.attachments is not None
            assert len(msg.attachments) == 2
            # Manifest items match email-channel shape.
            for m in msg.attachments:
                assert set(m.keys()) == {"filename", "path", "mime_type", "size"}
                assert os.path.isfile(m["path"])
            # Both files under one uuid dir under the session dir.
            parents = {os.path.dirname(m["path"]) for m in msg.attachments}
            assert len(parents) == 1
        finally:
            await _stop_channel(task)

    @pytest.mark.asyncio
    async def test_invalid_payload_emits_error_and_drops_message(self, tmp_uploads):
        q = asyncio.Queue()
        ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 21,
                              uploads_dir=str(tmp_uploads))
        task = await _start_channel(ch)
        try:
            async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 21}") as ws:
                await ws.send(json.dumps({
                    "content": "oversized image",
                    "command": None,
                    "attachments": [{
                        "filename": "huge.png",
                        "mime_type": "image/png",
                        "data": base64.b64encode(b"\x00" * (5 * 1024 * 1024 + 1)).decode(),
                    }],
                }))
                # Expect the server to send an error OutgoingMessage back.
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                data = json.loads(raw)
                assert data["final"] is True
                assert "5 MB" in data["content"] or "exceeds" in data["content"]

            # Nothing should have landed on the queue.
            assert q.empty()
        finally:
            await _stop_channel(task)

    @pytest.mark.asyncio
    async def test_no_attachments_key_still_queues_plain_message(self, tmp_uploads):
        q = asyncio.Queue()
        ch = WebSocketChannel(q, host=TEST_HOST, port=TEST_PORT + 22,
                              uploads_dir=str(tmp_uploads))
        task = await _start_channel(ch)
        try:
            async with websockets.connect(f"ws://{TEST_HOST}:{TEST_PORT + 22}") as ws:
                await ws.send(json.dumps({"content": "no files", "command": None}))
                await asyncio.sleep(0.1)
            msg = q.get_nowait()
            assert msg.content == "no files"
            assert msg.attachments is None
        finally:
            await _stop_channel(task)
```

- [ ] **Step 4.2: Run the tests and confirm they fail**

Run: `pytest tests/test_ws_channel.py::TestWsAttachmentsE2E -v`
Expected: FAIL — constructor doesn't accept `uploads_dir`; `_handle_connection` ignores `attachments`.

- [ ] **Step 4.3: Update `WebSocketChannel.__init__` to accept `uploads_dir`**

In `src/channels/ws.py`, change the signature:

```python
class WebSocketChannel:
    def __init__(
        self,
        in_queue: asyncio.Queue,
        host: str = "0.0.0.0",
        port: int = 8765,
        model: str = "",
        uploads_dir: str | None = None,
    ):
        self.in_queue = in_queue
        self.host = host
        self.port = port
        self.model = model
        self.uploads_dir = uploads_dir or os.path.join(os.getcwd(), "context", "uploads")
        self._connection: websockets.ServerConnection | None = None
```

- [ ] **Step 4.4: Update `_handle_connection` to parse, validate, stage, and error-reply**

Replace the body of the `async for raw in websocket:` loop:

```python
async for raw in websocket:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Received invalid JSON from client, ignoring")
        continue

    # Validate and decode attachments before touching anything else.
    decoded, err = _decode_attachments(data.get("attachments"))
    if err is not None:
        logger.info("Rejected inbound message: %s", err)
        await self.send(OutgoingMessage(
            content=f"Attachment rejected: {err}",
            channel="cli",
            session_id=SESSION_ID,
            reply_address={},
            final=True,
        ))
        continue

    manifest = _stage_attachments(decoded, SESSION_ID, self.uploads_dir) if decoded else None

    msg = IncomingMessage(
        content=data.get("content", ""),
        channel="cli",
        session_id=SESSION_ID,
        reply_address={},
        command=data.get("command") or None,
        attachments=manifest,
    )
    await self.in_queue.put(msg)
```

- [ ] **Step 4.5: Tiny docstring note on `src/channels/base.py`**

Add a one-liner above `IncomingMessage`:

```python
# attachments: list of {"filename": str, "path": str, "mime_type": str, "size": int}
#   — produced by ws.py (CLI uploads) and email.py (email attachments), same shape.
```

- [ ] **Step 4.6: Run the e2e tests and confirm they pass**

Run: `pytest tests/test_ws_channel.py::TestWsAttachmentsE2E -v`
Expected: PASS (3 tests).

- [ ] **Step 4.7: Run the full ws test file to catch regressions**

Run: `pytest tests/test_ws_channel.py -v`
Expected: all green. (The existing constructor-param tests must still work because `uploads_dir` has a default.)

- [ ] **Step 4.8: Commit**

```bash
git add src/channels/ws.py src/channels/base.py tests/test_ws_channel.py
git commit -m "feat(ws): accept CLI attachments, stage to uploads dir, error on bad input"
```

---

## Task 5: `build_multimodal_content` helper in `run.py`

**Files:**
- Modify: `run.py`
- Test: `tests/test_build_content.py` (**new**)

Pure helper: given `(text, attachments_manifest)`, return `str` when there are no attachments (backward compat), or a list of LiteLLM-compatible content blocks. Text attachments are inlined as text blocks wrapped in an `[Attachment: filename]` fenced preamble; images become `image_url` blocks with data URIs.

- [ ] **Step 5.1: Create `tests/test_build_content.py` with failing tests**

```python
"""Tests for run.build_multimodal_content."""
import base64
import os

import pytest


@pytest.fixture
def text_file(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("hello world")
    return p


@pytest.fixture
def image_file(tmp_path):
    p = tmp_path / "img.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    return p


def _att(path, mime):
    return {"filename": os.path.basename(path), "path": str(path),
            "mime_type": mime, "size": os.path.getsize(path)}


def test_no_attachments_returns_string():
    from run import build_multimodal_content
    assert build_multimodal_content("hi", None) == "hi"
    assert build_multimodal_content("hi", []) == "hi"


def test_single_image_produces_text_and_image_block(image_file):
    from run import build_multimodal_content
    blocks = build_multimodal_content("describe", [_att(image_file, "image/png")])
    assert isinstance(blocks, list)
    assert len(blocks) == 2
    assert blocks[0] == {"type": "text", "text": "describe"}
    assert blocks[1]["type"] == "image_url"
    url = blocks[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    # Round-trip base64 back to the file bytes.
    b64 = url.split(",", 1)[1]
    with open(image_file, "rb") as f:
        assert base64.b64decode(b64) == f.read()


def test_single_text_file_becomes_text_block(text_file):
    from run import build_multimodal_content
    blocks = build_multimodal_content("compare", [_att(text_file, "text/plain")])
    assert isinstance(blocks, list)
    assert blocks[0] == {"type": "text", "text": "compare"}
    assert blocks[1]["type"] == "text"
    assert "notes.txt" in blocks[1]["text"]
    assert "hello world" in blocks[1]["text"]


def test_empty_prompt_with_image_skips_leading_text_block(image_file):
    from run import build_multimodal_content
    blocks = build_multimodal_content("", [_att(image_file, "image/png")])
    assert len(blocks) == 1
    assert blocks[0]["type"] == "image_url"


def test_mixed_ordering_preserved(image_file, text_file):
    from run import build_multimodal_content
    blocks = build_multimodal_content(
        "look",
        [_att(image_file, "image/png"), _att(text_file, "text/plain")],
    )
    # text prompt, then image, then text attachment — ordering matters.
    types = [b["type"] for b in blocks]
    assert types == ["text", "image_url", "text"]
    assert blocks[0]["text"] == "look"
```

- [ ] **Step 5.2: Run the new tests and confirm they fail**

Run: `pytest tests/test_build_content.py -v`
Expected: FAIL — `build_multimodal_content` doesn't exist.

- [ ] **Step 5.3: Implement `build_multimodal_content` in `run.py`**

Add at module top:

```python
import base64
```

Add the function alongside `_build_content` (around run.py:143):

```python
def build_multimodal_content(text: str, attachments: list[dict] | None) -> str | list:
    """Build LiteLLM content from text + a staged-attachment manifest.

    Returns a plain `str` when there are no attachments (backward-compatible
    with the existing flow) or a list of content blocks otherwise.
    Images become `image_url` data-URI blocks; UTF-8 text files become
    fenced text blocks tagged with the filename.
    """
    if not attachments:
        return text

    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})

    for att in attachments:
        mime = att["mime_type"]
        path = att["path"]
        if mime.startswith("image/"):
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        else:
            with open(path, "rb") as f:
                content = f.read().decode("utf-8")  # pre-validated at ws layer
            blocks.append({
                "type": "text",
                "text": f"[Attachment: {att['filename']}]\n```\n{content}\n```",
            })

    return blocks
```

- [ ] **Step 5.4: Run the new tests and confirm they pass**

Run: `pytest tests/test_build_content.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5.5: Commit**

```bash
git add run.py tests/test_build_content.py
git commit -m "feat(run): add build_multimodal_content for LiteLLM vision blocks"
```

---

## Task 6: Branch `agent_worker` for CLI multimodal + purge uploads on reset

**Files:**
- Modify: `run.py` (`agent_worker`)
- Test: `tests/test_build_content.py` (integration-style tests exercising `agent_worker` via a fake queue + mocked agent).

Two changes in `agent_worker`:

1. **Content construction:** when `msg.channel == "cli"`, build content via `build_multimodal_content(msg.content, msg.attachments)`. For other channels, keep the existing `_build_content(msg)` path untouched.
2. **Reset cleanup:** on `clear` / `reset` commands — both the synchronous branch (run.py:175) and the mid-flight reset branch (run.py:261) — purge `<cwd>/context/uploads/<session_id>/` if it exists.

- [ ] **Step 6.1: Write failing tests for agent_worker behavior**

Append to `tests/test_build_content.py`:

```python
import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

from src.channels.base import IncomingMessage


@pytest.mark.asyncio
async def test_agent_worker_sends_multimodal_for_cli_channel(tmp_path, monkeypatch, image_file):
    """CLI inbound with attachments calls agent.handle with a list content."""
    import run as run_module
    from src.config import AgentConfig

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()

    captured: dict = {}
    fake_agent = type("A", (), {})()
    fake_agent.config = AgentConfig()

    async def fake_handle(content, session_id, **kwargs):
        captured["content"] = content
        return "ok"

    fake_agent.handle = fake_handle
    fake_agent.sessions = {}

    msg = IncomingMessage(
        content="look",
        channel="cli",
        session_id="cli",
        reply_address={},
        attachments=[_att(image_file, "image/png")],
    )
    await in_q.put(msg)

    task = asyncio.create_task(run_module.agent_worker(fake_agent, in_q, out_q))
    # Wait for one outgoing message
    out = await asyncio.wait_for(out_q.get(), timeout=2.0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert out.content == "ok"
    # The content passed to agent.handle was a list of multimodal blocks.
    assert isinstance(captured["content"], list)
    assert captured["content"][0]["type"] == "text"
    assert captured["content"][1]["type"] == "image_url"


@pytest.mark.asyncio
async def test_reset_command_purges_uploads_for_cli_session(tmp_path, monkeypatch):
    """`/clear` or `/reset` wipes context/uploads/<session_id>/."""
    import run as run_module
    from src.config import AgentConfig

    # Point run.py's cwd at tmp_path so uploads land there.
    monkeypatch.chdir(tmp_path)
    session_dir = Path("context/uploads/cli")
    session_dir.mkdir(parents=True)
    (session_dir / "leftover.txt").write_text("old")

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()

    fake_agent = type("A", (), {})()
    fake_agent.config = AgentConfig()
    fake_agent.sessions = {"cli": []}
    fake_agent.handle = AsyncMock(return_value="")

    await in_q.put(IncomingMessage(
        content="", channel="cli", session_id="cli", reply_address={}, command="clear",
    ))

    task = asyncio.create_task(run_module.agent_worker(fake_agent, in_q, out_q))
    await asyncio.wait_for(out_q.get(), timeout=2.0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert not session_dir.exists(), "uploads/<session_id>/ should be purged on /clear"
```

- [ ] **Step 6.2: Run and confirm the tests fail**

Run: `pytest tests/test_build_content.py -k "agent_worker or purge" -v`
Expected: FAIL — `agent_worker` doesn't branch on `cli` for multimodal, and doesn't purge uploads.

- [ ] **Step 6.3: Branch content construction in `agent_worker`**

In `run.py`, replace the line `content = _build_content(msg)` (currently around run.py:217) with:

```python
if msg.channel == "cli":
    content = build_multimodal_content(msg.content, msg.attachments)
else:
    content = _build_content(msg)
```

- [ ] **Step 6.4: Add uploads purge on `clear`/`reset` commands**

Add a helper near the top of `run.py`:

```python
import shutil


def _purge_session_uploads(session_id: str) -> None:
    """Remove context/uploads/<session_id>/ if it exists. Best-effort."""
    path = os.path.join(os.getcwd(), "context", "uploads", session_id)
    shutil.rmtree(path, ignore_errors=True)
```

In `agent_worker`, inside both reset branches:

```python
if msg.command in ("clear", "reset"):
    history = agent.sessions.pop(msg.session_id, None)
    if history and msg.command == "clear":
        asyncio.create_task(extract_learnings(agent.config, list(history)))
    if msg.channel == "cli":
        _purge_session_uploads(msg.session_id)
    await out_queue.put(OutgoingMessage(
        content="", channel=msg.channel, session_id=msg.session_id,
        reply_address=msg.reply_address,
    ))
    continue
```

And in the mid-flight reset branch (around run.py:261):

```python
if reset_msg:
    history = agent.sessions.pop(msg.session_id, None)
    if history and reset_msg.command == "clear":
        asyncio.create_task(extract_learnings(agent.config, list(history)))
    if reset_msg.channel == "cli":
        _purge_session_uploads(reset_msg.session_id)
    await out_queue.put(OutgoingMessage(
        content="", channel=reset_msg.channel, session_id=reset_msg.session_id,
        reply_address=reset_msg.reply_address,
    ))
    continue
```

- [ ] **Step 6.5: Run the new tests and confirm they pass**

Run: `pytest tests/test_build_content.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6.6: Run the full run-side test surface**

Run: `pytest tests/test_build_content.py tests/test_ws_channel.py tests/test_agent.py -v`
Expected: all green.

- [ ] **Step 6.7: Commit**

```bash
git add run.py tests/test_build_content.py
git commit -m "feat(run): route CLI attachments through multimodal content; purge uploads on reset"
```

---

## Task 7: CLI client — staging commands and client-side validation

**Files:**
- Modify: `cli.py`
- Test: `tests/test_cli_client.py`

Introduce a module-level `Staging` helper (pure, easy to unit-test) before touching the `run()` async loop:

- Size caps mirrored from `ws.py`.
- `Staging.add(path) -> str | None` returns an error string on rejection, else None; adds a record `{"filename", "mime_type", "data": <base64>, "size": int}` to the list.
- `Staging.remove(index_1_based)`, `Staging.clear()`, `Staging.list_display()`, `Staging.to_payload()` (returns the list of wire items) and `Staging.total_bytes`.
- MIME detection: `mimetypes.guess_type(path)`; if unrecognized, try UTF-8 decode; otherwise reject.

Then wire the commands into `run()`.

- [ ] **Step 7.1: Write failing tests for the `Staging` helper**

Append to `tests/test_cli_client.py`:

```python
import base64
import mimetypes

from cli import Staging  # to be created


class TestStaging:
    def test_add_image_succeeds(self, tmp_path):
        img = tmp_path / "x.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        s = Staging()
        err = s.add(str(img))
        assert err is None
        payload = s.to_payload()
        assert len(payload) == 1
        assert payload[0]["filename"] == "x.png"
        assert payload[0]["mime_type"] == "image/png"
        assert base64.b64decode(payload[0]["data"]).startswith(b"\x89PNG")

    def test_add_text_succeeds(self, tmp_path):
        p = tmp_path / "notes.txt"
        p.write_text("hello")
        s = Staging()
        assert s.add(str(p)) is None
        assert s.to_payload()[0]["mime_type"].startswith("text/")

    def test_add_missing_file_rejected(self, tmp_path):
        s = Staging()
        err = s.add(str(tmp_path / "nope.txt"))
        assert err and "not found" in err.lower()

    def test_add_oversized_image_rejected(self, tmp_path):
        p = tmp_path / "huge.png"
        p.write_bytes(b"\x00" * (5 * 1024 * 1024 + 1))
        s = Staging()
        err = s.add(str(p))
        assert err and "5" in err and "MB" in err

    def test_add_oversized_text_rejected(self, tmp_path):
        p = tmp_path / "huge.txt"
        p.write_bytes(b"x" * (256 * 1024 + 1))
        s = Staging()
        err = s.add(str(p))
        assert err and "256" in err and "KB" in err

    def test_total_cap_enforced(self, tmp_path):
        # Four 4 MB pngs fit (16 MB); a fifth 4 MB png pushes total over 20 MB.
        s = Staging()
        for i in range(4):
            p = tmp_path / f"a{i}.png"
            p.write_bytes(b"\x00" * (4 * 1024 * 1024))
            assert s.add(str(p)) is None
        p = tmp_path / "last.png"
        p.write_bytes(b"\x00" * (4 * 1024 * 1024 + 1))
        err = s.add(str(p))
        assert err and "20 MB" in err

    def test_binary_non_image_rejected(self, tmp_path):
        p = tmp_path / "weird.bin"
        p.write_bytes(b"\xff\xfe\xfa")
        s = Staging()
        err = s.add(str(p))
        assert err and ("UTF-8" in err or "recognized" in err.lower())

    def test_unknown_mime_but_utf8_accepted_as_text(self, tmp_path):
        p = tmp_path / "notes.weird"
        p.write_text("just text")
        # mimetypes returns None for .weird on most platforms
        assert mimetypes.guess_type(str(p))[0] is None
        s = Staging()
        assert s.add(str(p)) is None
        assert s.to_payload()[0]["mime_type"] == "text/plain"

    def test_remove_and_clear(self, tmp_path):
        p1 = tmp_path / "a.txt"
        p2 = tmp_path / "b.txt"
        p1.write_text("1")
        p2.write_text("2")
        s = Staging()
        s.add(str(p1))
        s.add(str(p2))
        assert len(s.to_payload()) == 2
        s.remove(1)  # 1-based: removes a.txt
        assert [item["filename"] for item in s.to_payload()] == ["b.txt"]
        s.clear()
        assert s.to_payload() == []

    def test_remove_out_of_range_returns_error(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("1")
        s = Staging()
        s.add(str(p))
        assert s.remove(0) and "index" in s.remove(0).lower()
        assert s.remove(5) and "index" in s.remove(5).lower()
```

- [ ] **Step 7.2: Run the tests and confirm they fail**

Run: `pytest tests/test_cli_client.py::TestStaging -v`
Expected: FAIL — `Staging` doesn't exist yet.

- [ ] **Step 7.3: Implement `Staging` in `cli.py`**

Add at module top (above `run()`):

```python
import base64
import mimetypes
import os

# Size caps mirrored from src/channels/ws.py
_MAX_IMAGE_BYTES = 5 * 1024 * 1024          # 5 MB
_MAX_TEXT_BYTES = 256 * 1024                # 256 KB
_MAX_TOTAL_BYTES = 20 * 1024 * 1024         # 20 MB
_ALLOWED_IMAGE_MIMES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
})


def _guess_mime(path: str) -> str | None:
    """Best-effort MIME detection: guess_type, then UTF-8 sniff as tiebreaker."""
    mime, _ = mimetypes.guess_type(path)
    if mime and mime != "application/octet-stream":
        return mime
    # Tiebreaker: try UTF-8 decode.
    try:
        with open(path, "rb") as f:
            f.read(4096).decode("utf-8")
        return "text/plain"
    except (OSError, UnicodeDecodeError):
        return None


class Staging:
    """Client-side staged attachments.

    `add(path)` returns None on success, or an error string. Enforces the
    same caps as ws.py so we fail fast without a round-trip.
    """

    def __init__(self) -> None:
        self._items: list[dict] = []  # each: {filename, mime_type, data, size}

    @property
    def total_bytes(self) -> int:
        return sum(it["size"] for it in self._items)

    def add(self, path: str) -> str | None:
        if not os.path.isfile(path):
            return f"{os.path.basename(path)}: file not found"
        size = os.path.getsize(path)
        mime = _guess_mime(path)
        if mime is None:
            return f"{os.path.basename(path)}: not a recognized image and not UTF-8 decodable"

        if mime.startswith("image/"):
            if mime not in _ALLOWED_IMAGE_MIMES:
                return f"{os.path.basename(path)}: unsupported image type {mime}"
            if size > _MAX_IMAGE_BYTES:
                return (f"{os.path.basename(path)} is "
                        f"{size / 1024 / 1024:.1f} MB (image cap is 5 MB)")
        else:
            if size > _MAX_TEXT_BYTES:
                return (f"{os.path.basename(path)} is "
                        f"{size / 1024:.0f} KB (text cap is 256 KB)")

        if self.total_bytes + size > _MAX_TOTAL_BYTES:
            return f"total staged would exceed 20 MB"

        with open(path, "rb") as f:
            raw = f.read()
        self._items.append({
            "filename": os.path.basename(path),
            "mime_type": mime,
            "data": base64.b64encode(raw).decode(),
            "size": size,
        })
        return None

    def remove(self, index_1_based: int) -> str | None:
        if not 1 <= index_1_based <= len(self._items):
            return f"index {index_1_based} out of range (have {len(self._items)})"
        self._items.pop(index_1_based - 1)
        return None

    def clear(self) -> None:
        self._items.clear()

    def list_display(self) -> str:
        if not self._items:
            return "no files staged"
        parts = []
        for i, it in enumerate(self._items, start=1):
            size = it["size"]
            if size >= 1024 * 1024:
                s = f"{size / 1024 / 1024:.1f} MB"
            else:
                s = f"{size / 1024:.0f} KB"
            parts.append(f"[{i}] {it['filename']} ({s})")
        return "staged: " + ", ".join(parts)

    def to_payload(self) -> list[dict]:
        """Return the wire-format list: {filename, mime_type, data} items."""
        return [
            {"filename": it["filename"], "mime_type": it["mime_type"], "data": it["data"]}
            for it in self._items
        ]
```

- [ ] **Step 7.4: Run the `TestStaging` tests and confirm they pass**

Run: `pytest tests/test_cli_client.py::TestStaging -v`
Expected: PASS (10 tests).

- [ ] **Step 7.5: Commit**

```bash
git add cli.py tests/test_cli_client.py
git commit -m "feat(cli): Staging helper with client-side validation for /attach"
```

---

## Task 8: Wire `/attach`, `/detach`, and batch-send into the CLI input loop

**Files:**
- Modify: `cli.py` (`run()` input loop)
- Test: `tests/test_cli_client.py`

Slash-command surface:

| Command | Action |
|---|---|
| `/attach <path> [<path>...]` | For each path, call `Staging.add`; print `📎 staged: <name> (<size>)` on success, `❌ rejected: <err>` on failure. |
| `/attach` (no args) | Print `Staging.list_display()`. |
| `/attach clear` | `Staging.clear()`, print `staging cleared`. |
| `/detach <n>` | `Staging.remove(n)`, print result or error. |

Send behavior:

- Non-slash input → payload `{"content": text, "command": None, "attachments": <staging.to_payload()>}`; staging cleared afterwards.
- Empty text with staged files: sent with `content=""` (spec-required).
- `/clear`, `/new`, `/reset`: also call `Staging.clear()` before sending the command.

- [ ] **Step 8.1: Write failing tests for the input-loop integration**

Append to `tests/test_cli_client.py`:

```python
@pytest.mark.asyncio
async def test_attach_then_send_includes_attachments(tmp_path):
    port = _BASE_PORT + 20
    received: list[dict] = []

    async def handler(ws: websockets.ServerConnection) -> None:
        async for raw in ws:
            received.append(json.loads(raw))
            await ws.send(json.dumps({
                "content": "ok", "tool_calls": [], "final": True, "attachments": None,
            }))

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    console = _make_console_with_input([
        f"/attach {img}",
        "describe this",
    ])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    # Only ONE payload should have been sent (the /attach command stays local).
    assert len(received) == 1
    payload = received[0]
    assert payload["content"] == "describe this"
    assert payload["command"] is None
    assert len(payload["attachments"]) == 1
    assert payload["attachments"][0]["filename"] == "x.png"
    assert payload["attachments"][0]["mime_type"] == "image/png"
    assert "data" in payload["attachments"][0]


@pytest.mark.asyncio
async def test_send_clears_staging(tmp_path):
    port = _BASE_PORT + 21
    received: list[dict] = []

    async def handler(ws):
        async for raw in ws:
            received.append(json.loads(raw))
            await ws.send(json.dumps({
                "content": "ok", "tool_calls": [], "final": True, "attachments": None,
            }))

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    console = _make_console_with_input([
        f"/attach {img}",
        "first send",   # staging cleared after this
        "second send",  # should go with no attachments
    ])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    assert len(received) == 2
    assert received[0]["attachments"] and len(received[0]["attachments"]) == 1
    assert not received[1].get("attachments")


@pytest.mark.asyncio
async def test_attach_clear_drops_staging_locally(tmp_path):
    port = _BASE_PORT + 22
    received: list[dict] = []

    async def handler(ws):
        async for raw in ws:
            received.append(json.loads(raw))
            await ws.send(json.dumps({
                "content": "ok", "tool_calls": [], "final": True, "attachments": None,
            }))

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    console = _make_console_with_input([
        f"/attach {img}",
        "/attach clear",
        "after clear",
    ])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    # Only one wire payload: the 'after clear' message, with no attachments.
    assert len(received) == 1
    assert received[0]["content"] == "after clear"
    assert not received[0].get("attachments")


@pytest.mark.asyncio
async def test_clear_command_also_clears_staging(tmp_path):
    port = _BASE_PORT + 23
    received: list[dict] = []

    async def handler(ws):
        async for raw in ws:
            received.append(json.loads(raw))
            await ws.send(json.dumps({
                "content": "ok", "tool_calls": [], "final": True, "attachments": None,
            }))

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    console = _make_console_with_input([
        f"/attach {img}",
        "/clear",
        "after reset",
    ])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    assert len(received) == 2
    # First wire message is the /clear; second is plain text with no attachments.
    assert received[0] == {"content": "", "command": "clear"}
    assert received[1]["content"] == "after reset"
    assert not received[1].get("attachments")


@pytest.mark.asyncio
async def test_attach_rejected_file_does_not_stage(tmp_path):
    port = _BASE_PORT + 24
    received: list[dict] = []

    async def handler(ws):
        async for raw in ws:
            received.append(json.loads(raw))
            await ws.send(json.dumps({
                "content": "ok", "tool_calls": [], "final": True, "attachments": None,
            }))

    console = _make_console_with_input([
        "/attach /nonexistent/path.png",
        "hi",
    ])

    async with websockets.serve(handler, "127.0.0.1", port):
        await cli.run("127.0.0.1", port, console=console)

    # Only "hi" went over the wire with no attachments.
    assert len(received) == 1
    assert received[0]["content"] == "hi"
    assert not received[0].get("attachments")
    # Output mentions rejection.
    output = console.file.getvalue()
    assert "rejected" in output or "not found" in output
```

- [ ] **Step 8.2: Run the tests and confirm they fail**

Run: `pytest tests/test_cli_client.py::test_attach_then_send_includes_attachments -v`
Expected: FAIL — `/attach` is unknown and gets sent as plain text.

- [ ] **Step 8.3: Wire `Staging` into `cli.py::run`**

Inside `run()`, before the main `while True:` loop, initialize staging:

```python
staging = Staging()
```

Replace the input-handling block (currently `if text == "/verbose":` through the `payload = {"content": text, ...}` assignment) with:

```python
if text == "/verbose":
    verbose = not verbose
    state = "on" if verbose else "off"
    console.print(f"[dim]Verbose mode {state}.[/dim]")
    continue

# /attach family — all local, never sent over the wire.
if text.startswith("/attach"):
    rest = text[len("/attach"):].strip()
    if rest == "":
        console.print(f"[dim]\U0001f4ce {staging.list_display()}[/dim]")
        continue
    if rest == "clear":
        staging.clear()
        console.print("[dim]staging cleared[/dim]")
        continue
    # Treat remainder as whitespace-separated list of paths.
    # (Shell-style quoting for paths with spaces is the user's responsibility;
    # prompt_toolkit delivers the raw line.)
    import shlex
    try:
        paths = shlex.split(rest)
    except ValueError as e:
        console.print(f"[red]❌ {e}[/red]")
        continue
    for p in paths:
        err = staging.add(p)
        if err:
            console.print(f"[red]❌ rejected: {err}[/red]")
        else:
            item = staging._items[-1]
            size = item["size"]
            sstr = f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.0f} KB"
            console.print(f"[dim]\U0001f4ce staged: {item['filename']} ({sstr})[/dim]")
    continue

if text.startswith("/detach"):
    rest = text[len("/detach"):].strip()
    try:
        idx = int(rest)
    except ValueError:
        console.print("[red]usage: /detach <index>[/red]")
        continue
    err = staging.remove(idx)
    if err:
        console.print(f"[red]❌ {err}[/red]")
    else:
        console.print("[dim]detached[/dim]")
    continue

if text in ("/clear", "/new"):
    staging.clear()
    payload = {"content": "", "command": "clear"}
elif text == "/reset":
    staging.clear()
    payload = {"content": "", "command": "reset"}
else:
    payload = {
        "content": text,
        "command": None,
        "attachments": staging.to_payload() or None,
    }
    staging.clear()
```

Also, allow the "empty prompt with staged files" case by removing the `if not text: continue` early-return only when staging is non-empty:

```python
text = line.strip()
if not text and not staging.to_payload():
    continue
```

- [ ] **Step 8.4: Run the new tests and confirm they pass**

Run: `pytest tests/test_cli_client.py -v`
Expected: all green, including the new `/attach` tests and existing CLI tests (the wire format gains an optional `attachments` field; old tests that check specific dicts still match because `attachments` is omitted when absent — verify by re-reading the assertions; if any old test does `assert received[0] == {"content": ..., "command": None}`, update it to `assert received[0]["content"] == ... and received[0]["command"] is None` or accept the extra key).

Note: existing tests `test_input_loop_sends_normal_message` and `test_input_loop_sends_clear_command` do exact-equality on the received dict. Update those assertions inline during this step to be key-subset checks so that the new (optional) `attachments` field doesn't break them:

```python
# OLD
assert received[0] == {"content": "hello world", "command": None}
# NEW
assert received[0]["content"] == "hello world"
assert received[0]["command"] is None
assert not received[0].get("attachments")
```

Apply the same pattern to `test_input_loop_sends_clear_command` and any other strict-dict checks.

- [ ] **Step 8.5: Commit**

```bash
git add cli.py tests/test_cli_client.py
git commit -m "feat(cli): /attach, /detach, and /attach clear commands with staging"
```

---

## Task 9: End-to-end integration test

**Files:**
- Test: `tests/test_cli_upload_integration.py` (**new**)

End-to-end path: CLI client → real WebSocket server (`WebSocketChannel`) → real `agent_worker` with a mocked LLM → assert that the LLM saw multimodal content and that the uploads dir exists; then send `/clear` and assert the session uploads dir is gone.

- [ ] **Step 9.1: Write the integration test**

```python
"""End-to-end integration: CLI → ws → agent_worker → mocked LLM."""
import asyncio
import base64
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import websockets

from src.agent.agent import Agent
from src.channels.ws import WebSocketChannel
from src.config import AgentConfig
from src.llm import LLMResponse
import run as run_module


@pytest.mark.asyncio
async def test_cli_upload_end_to_end(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # so context/uploads/ is under tmp_path
    # Identity file that Agent needs to build its system prompt.
    identity = tmp_path / "context" / "identity.md"
    identity.parent.mkdir(parents=True, exist_ok=True)
    identity.write_text("You are a test assistant.")
    config = AgentConfig(identity_file=identity, context_dir=tmp_path / "context")
    agent = Agent(config)

    in_q: asyncio.Queue = asyncio.Queue()
    out_q: asyncio.Queue = asyncio.Queue()
    ch = WebSocketChannel(in_q, host="127.0.0.1", port=19400, model=config.model)

    # Capture what the LLM is invoked with.
    llm_calls: list[list] = []

    async def fake_call_llm(model, messages, tools, **kwargs):
        llm_calls.append(messages)
        return LLMResponse(text="thanks for the picture", tool_calls=None)

    with patch("src.agent.agent.call_llm", new=fake_call_llm):
        server_task = asyncio.create_task(ch.start())
        worker_task = asyncio.create_task(run_module.agent_worker(agent, in_q, out_q))
        await asyncio.sleep(0.1)  # let server bind

        try:
            async with websockets.connect("ws://127.0.0.1:19400") as ws:
                # Build a tiny PNG as base64.
                png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
                payload = {
                    "content": "describe",
                    "command": None,
                    "attachments": [{
                        "filename": "t.png",
                        "mime_type": "image/png",
                        "data": base64.b64encode(png).decode(),
                    }],
                }
                await ws.send(json.dumps(payload))
                # Drain outbound — allow the agent to finalize.
                reply = await asyncio.wait_for(out_q.get(), timeout=3.0)
                assert reply.content == "thanks for the picture"

                # Verify LLM saw multimodal list content.
                user_msg = [m for m in llm_calls[-1] if m["role"] == "user"][-1]
                assert isinstance(user_msg["content"], list)
                assert any(b.get("type") == "image_url" for b in user_msg["content"])

                # Uploads dir exists for this session.
                uploads = tmp_path / "context" / "uploads" / "cli"
                assert uploads.exists()

                # Now send /clear and verify purge.
                await ws.send(json.dumps({"content": "", "command": "clear"}))
                await asyncio.wait_for(out_q.get(), timeout=2.0)

            # After the context manager exits the client disconnects, which
            # enqueues an 'extract' command — drain it.
            try:
                await asyncio.wait_for(out_q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

            assert not uploads.exists(), "uploads/<session_id>/ should be purged on /clear"
        finally:
            worker_task.cancel()
            server_task.cancel()
            for t in (worker_task, server_task):
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
```

- [ ] **Step 9.2: Run the integration test**

Run: `pytest tests/test_cli_upload_integration.py -v`
Expected: PASS.

- [ ] **Step 9.3: Run the full test suite**

Run: `pytest tests/ -v`
Expected: all green. If anything else fails, it's a regression from Tasks 1–8 and must be fixed before committing.

- [ ] **Step 9.4: Commit**

```bash
git add tests/test_cli_upload_integration.py
git commit -m "test: end-to-end CLI upload integration (ws → agent → multimodal)"
```

---

## Task 10: Manual vision smoke test against the configured model

**Files:** none (documentation step).

Spec `Open risks`: LiteLLM provider quirks on image blocks are worth a manual check before declaring the feature done. If the configured provider rejects OpenAI-shape `image_url` blocks with data URIs, the translation is isolated inside `build_multimodal_content` — adjust the block shape there; no caller changes.

- [ ] **Step 10.1: Boot the server with the default model**

```bash
source .venv/bin/activate
python run.py
```

- [ ] **Step 10.2: Connect the CLI and attach a small PNG**

In another terminal:

```bash
python cli.py --host localhost
> /attach /path/to/small-image.png
📎 staged: small-image.png (NN KB)
> what is in this image?
```

Expected: a coherent description that refers to the image content. If the model replies with "I can't see images" or LiteLLM raises, open `run.py`'s `build_multimodal_content` and try the Anthropic-native block shape (`{"type": "image", "source": {...}}`) behind a provider check. Record the fix in a follow-up commit.

- [ ] **Step 10.3: Exercise `/clear`**

```
> /clear
```

Then check: `ls context/uploads/cli/` — should be empty or not exist.

---

## Self-review notes

Spec coverage check:

| Spec section | Task | Notes |
|---|---|---|
| Wire protocol | 2, 4 | 3-field attachment items; exact-keys validation. |
| Server-side validation (rules 1-6) | 2 | Each rule maps to a `TestDecodeAttachments` case. |
| Server-side staging | 3 | Uuid per batch, whitespace normalization, collision suffix. |
| Cleanup (`/clear`, `/new`, `/reset`) | 6 | Purge both in the pre-handle reset branch and the mid-flight reset branch. |
| `build_multimodal_content` | 5 | Mirror of the spec's reference implementation. |
| Router wiring in `run.py` | 6 | Branch on `msg.channel == "cli"`. |
| Agent changes | 1 | Only `_estimate_chars` changes; handle already types `str | list`. |
| CLI UX | 7, 8 | Staging helper + slash-command wiring. |
| Size caps + MIMEs | 2, 7 | Constants in `ws.py`, mirrored in `cli.py`. |
| Testing matrix | 1–9 | Every row in the spec's test table has a task step. |
| Open risks / smoke test | 10 | Documented manual step; isolation point noted. |

**Placeholder scan:** no TBDs, no "handle edge cases", no "similar to above" — every test is spelled out with real bytes and assertions.

**Type consistency:**

- `_decode_attachments` returns `(list[dict] | None, str | None)` — callers always check `err is not None` (not `decoded is None`).
- `_stage_attachments` takes the decoded-items list (with `bytes` key) and returns the email-shape manifest (with `path`/`size`). Name is consistent across Tasks 3, 4, 6.
- `build_multimodal_content(text, attachments)` is the only name used in run.py and tests.
- `Staging.to_payload()` returns wire-format items (`filename`/`mime_type`/`data`); `Staging.add` records the full internal dict with extra `size`. Internals vs. wire are cleanly separated.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-23-cli-file-upload.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
