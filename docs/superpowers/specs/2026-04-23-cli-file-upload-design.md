# CLI File Upload — Design

**Date:** 2026-04-23
**Issue:** [#19](https://github.com/jalemieux/curunir/issues/19)
**Related:** [#30](https://github.com/jalemieux/curunir/issues/30) (outbound binary attachments, follow-up)

## Goal

Let a user attach files (images, UTF-8 text) to a message from the CLI so the agent can reason about them directly — including multimodal content for vision-capable models. Bring CLI inbound parity with what the email channel already supports.

## Non-goals

- Outbound binary attachments over the WebSocket (user can't download binaries produced by the agent when CLI is remote). Tracked in #30.
- PDF / DOCX / audio / video attachments. Add as a follow-up if demand appears.
- Streaming or chunked uploads.
- Changes to the email channel's attachment path. Kept untouched to avoid blast radius.
- Auto-preview of images inline in the terminal.

## Decisions locked during brainstorming

| # | Decision | Alternative considered | Why |
|---|---|---|---|
| 1 | Base64 over the WebSocket for upload bytes | Server reads the file via a path the CLI sends | CLI can target a remote server (`--host`), so a shared filesystem isn't guaranteed. |
| 2 | LLM sees images as inline multimodal content blocks (data-URI) | Save to disk only and let agent `read` it | Single-turn vision; no extra agent hop. |
| 3 | Text-only file types — images + UTF-8-decodable — no PDFs | Broader allowlist including PDF document blocks | KISS; PDFs can be a later addition. |
| 4 | `/attach` stages files, next prompt sends them in a batch | One file per `/attach`; space-separated on one line | Mirrors attachment UX people know from chat apps; composes with long prompts. |
| 5 | Size caps: 5 MB image, 256 KB text, 20 MB total per message | Env-configurable from day one | Hardcoded now, can be promoted to env vars if needed. |
| 6 | ws channel writes uploads to disk server-side so `IncomingMessage.attachments` has the same shape as email | Keep ws inline-bytes, branch in downstream helpers | Unified manifest across channels; one downstream content-building path. |

## Data flow

```
CLI                            ws.py              run.py (router)       agent.py        LLM
──                             ─────              ────────────────      ────────        ───
user: /attach img.png
      /attach notes.txt
      "compare these"

reads files, base64 ->
{content, attachments:[
  {filename, mime_type,
   data: <b64>}
]}      ─────ws──────►
                              validate shape + size
                              decode base64
                              write to uploads dir
                              build manifest
                                {filename, path,
                                 mime_type, size}
                              IncomingMessage(
                                content, attachments) ──►
                                                   build_multimodal_content():
                                                     - text blocks (decode UTF-8)
                                                     - image_url blocks (data URI)
                                                   Agent.handle(
                                                     message=list[block],
                                                     attachments=manifest) ──►
                                                                           history append
                                                                           call_llm ──────►
```

## Wire protocol

Inbound WebSocket JSON from CLI to server gains an `attachments` array:

```json
{
  "content": "compare these two",
  "command": null,
  "attachments": [
    {"filename": "img.png", "mime_type": "image/png", "data": "<base64>"},
    {"filename": "notes.txt", "mime_type": "text/plain", "data": "<base64>"}
  ]
}
```

Each item has exactly three fields: `filename` (str), `mime_type` (str), `data` (base64 str). No other keys are honored.

## Server-side validation (in `ws.py`)

Before putting anything on `in_queue`, the channel validates each incoming payload. On **any** failure, the whole message is dropped and the user gets a single error `OutgoingMessage` explaining what went wrong — no partial staging, no retries.

1. `attachments` is a list (or absent). Missing → treat as no attachments.
2. Each item has `filename`, `mime_type`, `data` of the correct types.
3. `data` is valid base64. Decoded size ≤ **5 MB** if MIME starts `image/`, else ≤ **256 KB**.
4. Total decoded size across all items ≤ **20 MB**.
5. Image MIME ∈ {`image/png`, `image/jpeg`, `image/gif`, `image/webp`}. Other `image/*` rejected.
6. Non-image items must decode cleanly as UTF-8 after base64 decode; otherwise rejected.

## Server-side staging

For each valid payload, the ws channel writes decoded bytes to:

```
<project_root>/context/uploads/<session_id>/<uuid>/<filename>
```

`<uuid>` is generated once per inbound message, so a batch of files lands together under the same directory. `<filename>` is used as-is after the same Unicode-whitespace normalization the email channel performs (`_normalize_unicode_whitespace`). If two files in the same batch collide on filename, suffix with `_1`, `_2`, etc.

The `IncomingMessage.attachments` manifest is then:

```python
[
  {"filename": "img.png", "path": "/abs/.../context/uploads/<sid>/<uuid>/img.png",
   "mime_type": "image/png", "size": 2145678},
  ...
]
```

**This is the exact same shape the email channel produces** — downstream code doesn't care which channel produced it.

## Cleanup

On `/clear` or `/new` (session-reset commands), `context/uploads/<session_id>/` is purged recursively before the rest of the reset runs. On `/reset` (reset without learnings extraction), same purge.

Boot-time cleanup is out of scope — the staging dir won't grow unbounded in normal use because every reset clears it, and re-running the server on a crashed session is rare enough not to warrant a sweep. Can be added later if it bites.

## Building LLM content — `build_multimodal_content`

New helper in `run.py`:

```python
def build_multimodal_content(text: str, attachments: list[dict] | None) -> str | list:
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
                content = f.read().decode("utf-8")  # pre-validated at ws
            blocks.append({
                "type": "text",
                "text": f"[Attachment: {att['filename']}]\n```\n{content}\n```",
            })

    return blocks
```

Returns `str` when there are no attachments (backward-compatible with existing flows) or a `list` of LiteLLM-compatible content blocks when there are.

## Router wiring in `run.py`

Inside `agent_worker` (run.py:164), the content dispatch branches by channel:

- `msg.channel == "cli"` → `content = build_multimodal_content(text, msg.attachments)`
- other channels → unchanged: `content = _build_content(msg)` with `_enrich_attachments` still inlining text attachments into the prompt string

The email channel keeps its existing path. No attempt to converge the two paths in this spec.

## Agent changes — `src/agent/agent.py`

`Agent.handle()` already types `message` as `str | list`. Work to do:

1. When `message` is a list, store it as-is on `self.history[...]["content"]`.
2. Pass through to `call_llm` untouched — LiteLLM already supports list content on messages.
3. Extend `_trim_history` (the 250k-char limit) to count list content: sum `len(block["text"])` for text blocks, and use a fixed per-image cost (conservatively **2000 chars per image block**) so images age out of history alongside text on long sessions.
4. No other changes to the tool-call loop, context-overflow retry, or delegation.

## CLI UX — `cli.py`

**Slash commands:**

| Command | Behavior |
|---|---|
| `/attach <path>` | Read, validate locally, add to staging. Print `📎 staged: <filename> (<size>)`. |
| `/attach <path1> <path2> …` | Multiple paths per command (shell quoting for spaces). |
| `/attach` (no args) | List staged: `📎 staged: [1] img.png (2.1 MB), [2] notes.txt (12 KB)`. |
| `/detach <n>` | Remove by 1-based index. |
| `/attach clear` | Drop all staged files. |

**Send behavior:**

- The next non-slash input gets sent as `{"content": <text>, "attachments": [<staged>]}`, after which the staging list is cleared.
- Empty prompt with staged files is allowed — sends `{"content": "", "attachments": [...]}`.
- `/clear`, `/new`, `/reset` also drop staging (matches server-side purge).

**Client-side validation** — same rules as server, fail fast without a round trip:

- File exists and is readable.
- Size within per-type cap.
- Running total of staged ≤ 20 MB.
- MIME detection: `mimetypes.guess_type(path)`; if `application/octet-stream` or unrecognized, attempt UTF-8 decode of the bytes as the tiebreaker. If both fail, reject.

**Error shape:**

```
> /attach screenshot.png
📎 staged: screenshot.png (2.1 MB)

> /attach huge.bin
❌ rejected: huge.bin is 8.2 MB (image cap is 5 MB)

> /attach weird.bin
❌ rejected: weird.bin is not a recognized image and not UTF-8 decodable
```

One-line feedback, no confirmation prompts.

## Size limits (hardcoded constants)

| Limit | Value |
|---|---|
| Per image | 5 MB |
| Per text file | 256 KB |
| Total per message | 20 MB |
| Image MIMEs | `image/png`, `image/jpeg`, `image/gif`, `image/webp` |

These live as module constants in `ws.py` and (mirrored) `cli.py`. Promotion to env vars is a trivial future change if needed.

## Testing

| File | Coverage |
|---|---|
| `tests/test_ws_channel.py` (extend) | Valid payload → IncomingMessage manifest + files on disk. Invalid shape / bad base64 / oversized / disallowed MIME → user gets error, nothing staged. Batch of 2+ files under one uuid dir. |
| `tests/test_build_content.py` (new) | No attachments → `str`. One image → `[text, image_url]`. One text file → `[text, text-fence]`. Mixed → ordering. Empty user text + one image → single block without leading text. |
| `tests/test_agent.py` (extend) | `Agent.handle(message=list)` → history holds list, mock `call_llm` receives list. `_trim_history` accounts for image cost. |
| `tests/test_cli_client.py` (new) | `/attach`, `/attach clear`, `/detach`, batch send, staging cleared after send, `/clear` purges staging, client-side validation errors surface locally (via a fake ws server fixture). |
| Integration | End-to-end: stage image + text → send prompt → mock LLM receives multimodal → `context/uploads/<session>/` exists → `/clear` purges it. |

Follow existing project conventions: pytest-asyncio, `patch("src.agent.agent.call_llm", new_callable=AsyncMock)`, isolation fixtures. Add a `tmp_uploads` fixture that points the ws channel at a temp dir.

## Files to change

| File | Change |
|---|---|
| `src/channels/ws.py` | Parse `attachments` from inbound JSON. Validate. Decode base64. Write to uploads dir. Build email-shaped manifest. Emit user-facing error on failure. |
| `src/channels/base.py` | Docstring note on the attachment dict schema (no structural change). |
| `run.py` | New `build_multimodal_content`. Branch in `agent_worker` so ws channel uses it; other channels unchanged. Purge `context/uploads/<session>/` on reset commands. |
| `src/agent/agent.py` | Allow list-type user content in history; extend `_trim_history` to cost list content. |
| `cli.py` | `/attach`, `/detach`, `/attach clear`, staging list, batch send, client-side validation, error rendering. |
| `tests/test_ws_channel.py` | Extend. |
| `tests/test_build_content.py` | New. |
| `tests/test_agent.py` | Extend. |
| `tests/test_cli_client.py` | New. |
| `tests/conftest.py` | Add `tmp_uploads` fixture if multiple test files need it. |

## Open risks / things to watch at implementation time

- **LiteLLM provider quirks on image blocks.** The OpenAI-shape `image_url` with a `data:` URI is accepted by Anthropic-via-LiteLLM per current docs, but worth a manual smoke test against the configured `MODEL` before declaring the feature done. If the target provider needs a different block shape, isolate the translation inside `build_multimodal_content` — no caller changes.
- **History trimming and multimodal content.** The 2000-char-per-image heuristic is a placeholder. If we observe long multimodal sessions running out of context, revisit.
- **Upload dir growth on crash.** Session-reset commands clean up, but an ungraceful server restart can orphan directories. Acceptable for now; revisit if operators complain.
