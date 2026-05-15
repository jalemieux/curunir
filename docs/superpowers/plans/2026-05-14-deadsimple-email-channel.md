# Deadsimple Email Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Gmail-backed `EmailChannel` with one that talks to deadsimple.email's HTTP API. The channel's public surface (class name, `Channel` protocol, `channel="email"`, `session_id=<thread>`) stays identical; the transport, processed-state tracking, and config all change.

**Architecture:** A new `src/channels/deadsimple.py` HTTP client replaces `src/channels/gmail.py` one-for-one. `EmailChannel` is rewritten in place to use it. Polling is cursor-paginated newest-first, terminating at a `(created_at, message_id)` watermark persisted to `context/email_state.json`. Threading uses deadsimple's server-assigned `thread_id` as `session_id`. Replies hit `/messages/{id}/reply` for text-only and `/messages` (with explicit `in_reply_to` + `references` + base64 attachments) when files are attached. Gmail is deleted entirely.

**Tech Stack:** Python 3.12+ async, `httpx.AsyncClient` for HTTP, `pytest` + `pytest-asyncio` + `respx` (new test dep) for HTTP mocking. No new runtime dependencies — `httpx` is already used elsewhere.

**Spec:** `docs/superpowers/specs/2026-05-14-deadsimple-email-channel-design.md`

---

## Background — What Exists Today

Read these first so the implementation matches existing patterns:

- `src/channels/email.py` — `EmailChannel` class, polls Gmail every N seconds, queues `IncomingMessage`, sends via `gmail.send_reply`. Reused helpers: nothing — the file is being rewritten.
- `src/channels/gmail.py` — Gmail HTTP-via-Google-SDK client. **Deleted in Task 11.** Outbound allowlist (`_check_recipients_allowed`) moves to `deadsimple.py`.
- `src/channels/_attachments.py` — `_normalize_unicode_whitespace`, `_validate_attachment_metadata`. **Provider-agnostic, kept as-is, reused.**
- `src/channels/base.py` — `IncomingMessage`, `OutgoingMessage`, `Channel` protocol. **Unchanged.**
- `src/config.py` — `EmailChannelConfig` dataclass. Fields swap, structure stays.
- `run.py:519-531` — `EmailChannelConfig` hydration from env + conditional `EmailChannel` instantiation. Updated in Task 10.
- `tests/test_email_channel.py` — existing async tests, mock `gmail.build_service` with `MagicMock`. **Rewritten in Tasks 7–10 against the new code.**
- `tests/test_gmail.py` — Gmail client tests. **Deleted in Task 11.**
- `tests/conftest.py` — `tmp_context`, `tmp_skills`, `agent_config` fixtures.

## Deadsimple API summary (for reference inside tasks)

Base URL: `https://api.deadsimple.email`. Auth: `Authorization: Bearer dse_...`. JSON in/out.

| Method | Path | Use |
|---|---|---|
| `GET` | `/v1/inboxes/{inbox_id}` | Validate inbox at startup |
| `GET` | `/v1/inboxes/{inbox_id}/messages?limit&cursor` | List newest-first, both directions |
| `GET` | `/v1/inboxes/{inbox_id}/messages/{message_id}` | Full message detail |
| `POST` | `/v1/inboxes/{inbox_id}/messages` | Send (used when reply carries attachments, or for fresh sends) |
| `POST` | `/v1/inboxes/{inbox_id}/messages/{message_id}/reply` | Reply with auto-set threading (no attachments) |
| `GET` | `/v1/inboxes/{inbox_id}/messages/{message_id}/attachments/{attachment_id}` | Returns `{download_url: <signed-url>}` (assumed shape; 1h TTL) |

Message shape (fields we use):

```json
{
  "message_id": "<uuid>",
  "thread_id": "<uuid>",
  "direction": "inbound" | "outbound",
  "from_email": "alice@example.com",
  "to": ["bot@deadsimple.email"],
  "cc": [],
  "subject": "Help with X",
  "text_body": "...",
  "html_body": "...",
  "attachments": [{"attachment_id": "<uuid>", "filename": "report.pdf", "content_type": "application/pdf", "size": 12345}],
  "is_spam": false,
  "spam_score": 0.4,
  "created_at": "2026-05-14T15:30:00Z"
}
```

List response shape (assumed; verify on first integration run):

```json
{"data": [<Message>, ...], "next_cursor": "<opaque>" | null}
```

Rate-limit headers on every response: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (Unix epoch seconds).

Idempotency: `Idempotency-Key` header on POSTs caches the response for 24h.

---

## File Structure

**New files:**
- `src/channels/deadsimple.py` — HTTP client (`DeadsimpleClient`, `DeadsimpleError`)
- `src/channels/_email_state.py` — `EmailState` watermark persistence
- `tests/test_deadsimple.py` — unit tests for the HTTP client (respx)
- `tests/test_email_state.py` — unit tests for watermark persistence

**Rewritten files:**
- `src/channels/email.py` — `EmailChannel` on top of `DeadsimpleClient`
- `tests/test_email_channel.py` — tests against new channel
- `src/config.py` — `EmailChannelConfig` field swap
- `.env.example` — env var swap
- `docs/architecture.md` — channel description + ADR + changelog
- `README.md` — email setup snippet
- `requirements.txt` — drop `google-auth`, `google-api-python-client`; add `respx` (test only)

**Deleted files:**
- `src/channels/gmail.py`
- `tests/test_gmail.py`
- `docs/gmail-setup.md` (replaced by a short paragraph in README)

---

## Task 1: `EmailState` watermark persistence

**Files:**
- Create: `src/channels/_email_state.py`
- Create: `tests/test_email_state.py`

Tiny, self-contained, no HTTP. Lays the foundation for the polling loop's idempotency.

- [ ] **Step 1: Create the failing test file**

Create `tests/test_email_state.py`:

```python
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
```

- [ ] **Step 2: Verify the tests fail**

Run: `pytest tests/test_email_state.py -v`
Expected: `ImportError` / `ModuleNotFoundError` for `src.channels._email_state`.

- [ ] **Step 3: Implement `EmailState`**

Create `src/channels/_email_state.py`:

```python
"""Persistent watermark for deadsimple email polling.

The watermark is a (created_at, message_id) tuple — created_at alone is
insufficient because deadsimple may emit two messages with identical
timestamps, in which case message_id lexicographic order breaks the tie.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class EmailState:
    path: Path
    watermark_created_at: datetime | None = None
    watermark_message_id: str = ""

    @classmethod
    def load(cls, path: Path) -> "EmailState":
        """Load watermark from disk. Missing/corrupt files yield a blank state."""
        state = cls(path=path)
        try:
            raw = json.loads(path.read_text())
            ts = raw.get("watermark_created_at")
            if ts:
                state.watermark_created_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            state.watermark_message_id = raw.get("watermark_message_id", "") or ""
        except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError):
            pass
        return state

    def set_watermark(self, created_at: datetime, message_id: str) -> None:
        self.watermark_created_at = created_at
        self.watermark_message_id = message_id

    def is_after_watermark(self, created_at: datetime, message_id: str) -> bool:
        """True iff (created_at, message_id) sorts strictly after the watermark."""
        if self.watermark_created_at is None:
            return True
        return (created_at, message_id) > (self.watermark_created_at, self.watermark_message_id)

    def save(self) -> None:
        """Atomic write via temp-file + os.replace."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "watermark_created_at": (
                self.watermark_created_at.isoformat() if self.watermark_created_at else None
            ),
            "watermark_message_id": self.watermark_message_id,
        }
        fd, tmp_path = tempfile.mkstemp(dir=self.path.parent, prefix=".email_state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f)
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
```

- [ ] **Step 4: Verify tests pass**

Run: `pytest tests/test_email_state.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channels/_email_state.py tests/test_email_state.py
git commit -m "feat(email): add EmailState watermark persistence"
```

---

## Task 2: `DeadsimpleClient` skeleton + auth + 429 retry

**Files:**
- Create: `src/channels/deadsimple.py`
- Create: `tests/test_deadsimple.py`
- Modify: `requirements.txt`

Lay down the HTTP client shell: constructor, `_request` helper that does auth + JSON + rate-limit retry. Subsequent tasks plug methods into this shell.

- [ ] **Step 1: Add respx to test dependencies**

Edit `requirements.txt`. Append (do not touch existing lines):

```
respx
```

- [ ] **Step 2: Install the new dep**

Run: `pip install respx`
Expected: success.

- [ ] **Step 3: Write the failing tests**

Create `tests/test_deadsimple.py`:

```python
import time
from unittest.mock import patch

import httpx
import pytest
import respx

from src.channels.deadsimple import DeadsimpleClient, DeadsimpleError


@pytest.fixture
def client():
    return DeadsimpleClient(
        api_key="dse_test_key",
        api_base="https://api.deadsimple.email",
        inbox_id="inbox-uuid-1",
        allowed_recipients=[],
        restrict_outbound=False,
    )


@pytest.mark.asyncio
@respx.mock
async def test_request_sets_auth_header(client):
    route = respx.get("https://api.deadsimple.email/v1/inboxes/inbox-uuid-1").mock(
        return_value=httpx.Response(200, json={"data": {"inbox_id": "inbox-uuid-1"}})
    )
    await client.validate_inbox()
    assert route.called
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer dse_test_key"


@pytest.mark.asyncio
@respx.mock
async def test_validate_inbox_raises_on_404(client):
    respx.get("https://api.deadsimple.email/v1/inboxes/inbox-uuid-1").mock(
        return_value=httpx.Response(404, json={"error": {"code": "not_found", "message": "no"}})
    )
    with pytest.raises(DeadsimpleError) as exc:
        await client.validate_inbox()
    assert "404" in str(exc.value) or "not_found" in str(exc.value)


@pytest.mark.asyncio
@respx.mock
async def test_request_retries_once_on_429(client):
    reset_at = int(time.time()) + 1
    route = respx.get("https://api.deadsimple.email/v1/inboxes/inbox-uuid-1").mock(
        side_effect=[
            httpx.Response(429, headers={"X-RateLimit-Reset": str(reset_at)}, json={"error": {"code": "rate_limited"}}),
            httpx.Response(200, json={"data": {"inbox_id": "inbox-uuid-1"}}),
        ]
    )
    with patch("src.channels.deadsimple.asyncio.sleep", new_callable=_AsyncNoop):
        await client.validate_inbox()
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_request_raises_after_second_429(client):
    route = respx.get("https://api.deadsimple.email/v1/inboxes/inbox-uuid-1").mock(
        return_value=httpx.Response(429, headers={"X-RateLimit-Reset": "0"}, json={"error": {"code": "rate_limited"}})
    )
    with patch("src.channels.deadsimple.asyncio.sleep", new_callable=_AsyncNoop):
        with pytest.raises(DeadsimpleError):
            await client.validate_inbox()
    assert route.call_count == 2


class _AsyncNoop:
    """patch helper: replaces asyncio.sleep with an awaitable that returns immediately."""
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, delay: float):
        self.calls.append(delay)
```

- [ ] **Step 4: Verify tests fail**

Run: `pytest tests/test_deadsimple.py -v`
Expected: `ImportError` / `ModuleNotFoundError`.

- [ ] **Step 5: Implement the client skeleton**

Create `src/channels/deadsimple.py`:

```python
"""HTTP client for deadsimple.email's REST API.

Scope: only the endpoints the email channel needs. Auth, 429 retry, and
outbound-recipient allowlisting live here so the channel layer stays
focused on translating between deadsimple messages and curunir messages.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class DeadsimpleError(Exception):
    """Raised when a deadsimple API call fails."""


class DeadsimpleClient:
    def __init__(
        self,
        api_key: str,
        api_base: str,
        inbox_id: str,
        allowed_recipients: list[str],
        restrict_outbound: bool,
        timeout_sec: float = 30.0,
    ):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.inbox_id = inbox_id
        self.allowed_recipients = allowed_recipients
        self.restrict_outbound = restrict_outbound
        self._http = httpx.AsyncClient(timeout=timeout_sec)

    async def aclose(self) -> None:
        await self._http.aclose()

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """One-shot request with a single 429 retry honoring X-RateLimit-Reset."""
        url = f"{self.api_base}{path}"
        for attempt in (0, 1):
            try:
                resp = await self._http.request(
                    method,
                    url,
                    headers=self._headers(idempotency_key),
                    json=json_body,
                    params=params,
                )
            except httpx.HTTPError as e:
                raise DeadsimpleError(f"HTTP error on {method} {path}: {e}") from e

            if resp.status_code == 429 and attempt == 0:
                reset = resp.headers.get("X-RateLimit-Reset")
                delay = max(1.0, float(reset) - time.time()) if reset else 5.0
                logger.warning("deadsimple 429 on %s %s, sleeping %.1fs", method, path, delay)
                await asyncio.sleep(min(delay, 60.0))
                continue

            if resp.status_code >= 400:
                raise DeadsimpleError(
                    f"deadsimple {method} {path} returned {resp.status_code}: {resp.text[:500]}"
                )

            if not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError as e:
                raise DeadsimpleError(f"non-JSON response from {method} {path}: {e}") from e

        raise DeadsimpleError(f"giving up on {method} {path} after 429 retry")

    # --- public methods (filled in by later tasks) ---

    async def validate_inbox(self) -> dict[str, Any]:
        """Confirm the configured inbox exists and is accessible. Returns inbox JSON."""
        return await self._request("GET", f"/v1/inboxes/{self.inbox_id}")
```

- [ ] **Step 6: Verify tests pass**

Run: `pytest tests/test_deadsimple.py -v`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add src/channels/deadsimple.py tests/test_deadsimple.py requirements.txt
git commit -m "feat(email): add DeadsimpleClient skeleton with auth + 429 retry"
```

---

## Task 3: List + detail message endpoints

**Files:**
- Modify: `src/channels/deadsimple.py`
- Modify: `tests/test_deadsimple.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_deadsimple.py`:

```python
@pytest.mark.asyncio
@respx.mock
async def test_list_messages_single_page(client):
    respx.get("https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages").mock(
        return_value=httpx.Response(200, json={
            "data": [
                {"message_id": "m1", "thread_id": "t1", "direction": "inbound",
                 "from_email": "a@x.com", "subject": "hi", "created_at": "2026-05-14T15:30:00Z"},
                {"message_id": "m2", "thread_id": "t2", "direction": "outbound",
                 "from_email": "bot@x.com", "subject": "re: hi", "created_at": "2026-05-14T15:29:00Z"},
            ],
            "next_cursor": None,
        })
    )
    page = await client.list_messages(limit=50)
    assert len(page["data"]) == 2
    assert page["next_cursor"] is None


@pytest.mark.asyncio
@respx.mock
async def test_list_messages_passes_cursor(client):
    route = respx.get("https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages").mock(
        return_value=httpx.Response(200, json={"data": [], "next_cursor": None})
    )
    await client.list_messages(limit=20, cursor="cur-abc")
    sent = route.calls.last.request
    assert sent.url.params["limit"] == "20"
    assert sent.url.params["cursor"] == "cur-abc"


@pytest.mark.asyncio
@respx.mock
async def test_get_message_returns_detail(client):
    respx.get(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages/m1"
    ).mock(return_value=httpx.Response(200, json={
        "data": {
            "message_id": "m1", "thread_id": "t1", "direction": "inbound",
            "from_email": "a@x.com", "subject": "hi",
            "text_body": "Hello there", "html_body": "<p>Hello</p>",
            "attachments": [],
            "created_at": "2026-05-14T15:30:00Z",
        }
    }))
    msg = await client.get_message("m1")
    assert msg["message_id"] == "m1"
    assert msg["text_body"] == "Hello there"
```

- [ ] **Step 2: Verify tests fail**

Run: `pytest tests/test_deadsimple.py -v -k "list_messages or get_message"`
Expected: `AttributeError` for `list_messages` and `get_message`.

- [ ] **Step 3: Implement the endpoints** — append to `src/channels/deadsimple.py`:

```python
    async def list_messages(self, *, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        """Single page of messages, newest-first per the API's default ordering."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return await self._request(
            "GET", f"/v1/inboxes/{self.inbox_id}/messages", params=params
        )

    async def get_message(self, message_id: str) -> dict[str, Any]:
        """Full message detail: text_body, html_body, attachments[], etc."""
        resp = await self._request(
            "GET", f"/v1/inboxes/{self.inbox_id}/messages/{message_id}"
        )
        return resp.get("data", resp)
```

- [ ] **Step 4: Verify tests pass**

Run: `pytest tests/test_deadsimple.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channels/deadsimple.py tests/test_deadsimple.py
git commit -m "feat(email): add list_messages + get_message to DeadsimpleClient"
```

---

## Task 4: Attachment download (signed-URL flow)

**Files:**
- Modify: `src/channels/deadsimple.py`
- Modify: `tests/test_deadsimple.py`

The attachments endpoint returns a signed URL that we then GET (no auth header) to retrieve bytes.

- [ ] **Step 1: Write the failing tests** — append:

```python
@pytest.mark.asyncio
@respx.mock
async def test_download_attachment_writes_bytes(client, tmp_path):
    respx.get(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages/m1/attachments/a1"
    ).mock(return_value=httpx.Response(200, json={
        "data": {"download_url": "https://signed.example.com/file.pdf?token=xyz"}
    }))
    respx.get("https://signed.example.com/file.pdf").mock(
        return_value=httpx.Response(200, content=b"PDF-BYTES-HERE")
    )

    dest = tmp_path / "report.pdf"
    await client.download_attachment("m1", "a1", dest)

    assert dest.exists()
    assert dest.read_bytes() == b"PDF-BYTES-HERE"


@pytest.mark.asyncio
@respx.mock
async def test_download_attachment_raises_when_url_fetch_fails(client, tmp_path):
    respx.get(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages/m1/attachments/a1"
    ).mock(return_value=httpx.Response(200, json={
        "data": {"download_url": "https://signed.example.com/file.pdf"}
    }))
    respx.get("https://signed.example.com/file.pdf").mock(
        return_value=httpx.Response(403, text="expired")
    )

    with pytest.raises(DeadsimpleError):
        await client.download_attachment("m1", "a1", tmp_path / "out.pdf")
```

- [ ] **Step 2: Verify tests fail**

Run: `pytest tests/test_deadsimple.py -v -k "download_attachment"`
Expected: `AttributeError`.

- [ ] **Step 3: Implement** — append:

```python
    async def download_attachment(
        self, message_id: str, attachment_id: str, dest: Path
    ) -> None:
        """Two-step download: ask for a signed URL, GET the bytes, write to dest."""
        resp = await self._request(
            "GET",
            f"/v1/inboxes/{self.inbox_id}/messages/{message_id}/attachments/{attachment_id}",
        )
        data = resp.get("data", resp)
        url = data.get("download_url") or data.get("url")
        if not url:
            raise DeadsimpleError(
                f"attachment {attachment_id} response missing download_url: {data}"
            )

        try:
            r = await self._http.get(url)
        except httpx.HTTPError as e:
            raise DeadsimpleError(f"attachment fetch failed: {e}") from e
        if r.status_code >= 400:
            raise DeadsimpleError(
                f"attachment fetch returned {r.status_code} for {attachment_id}"
            )

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
```

- [ ] **Step 4: Verify tests pass**

Run: `pytest tests/test_deadsimple.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channels/deadsimple.py tests/test_deadsimple.py
git commit -m "feat(email): add attachment download to DeadsimpleClient"
```

---

## Task 5: Outbound — reply (text-only) and send (with attachments)

**Files:**
- Modify: `src/channels/deadsimple.py`
- Modify: `tests/test_deadsimple.py`

`send_reply` hits `/messages/{id}/reply` for plain text. `send_with_attachments` hits `/messages` with `in_reply_to` + `references` + base64 attachments. Allowlist is enforced on both before any HTTP call.

- [ ] **Step 1: Write the failing tests** — append:

```python
@pytest.fixture
def restricted_client():
    return DeadsimpleClient(
        api_key="dse_test",
        api_base="https://api.deadsimple.email",
        inbox_id="inbox-uuid-1",
        allowed_recipients=["alice@example.com"],
        restrict_outbound=True,
    )


@pytest.mark.asyncio
@respx.mock
async def test_send_reply_posts_to_reply_endpoint(client):
    route = respx.post(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages/m1/reply"
    ).mock(return_value=httpx.Response(201, json={"data": {"message_id": "m2"}}))
    await client.send_reply(
        in_reply_to="m1", to="alice@example.com", text_body="Hi back"
    )
    assert route.called
    req = route.calls.last.request
    assert req.headers["idempotency-key"] == "reply-m1"
    import json as _json
    parsed = _json.loads(req.content.decode())
    assert parsed["text_body"] == "Hi back"


@pytest.mark.asyncio
@respx.mock
async def test_send_reply_blocked_by_allowlist(restricted_client):
    respx.post("https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages/m1/reply").mock(
        return_value=httpx.Response(201, json={})
    )
    with pytest.raises(DeadsimpleError) as exc:
        await restricted_client.send_reply(
            in_reply_to="m1", to="evil@evil.com", text_body="hi"
        )
    assert "Outbound" in str(exc.value)


@pytest.mark.asyncio
@respx.mock
async def test_send_with_attachments_uses_messages_endpoint(client, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_bytes(b"hello")

    route = respx.post(
        "https://api.deadsimple.email/v1/inboxes/inbox-uuid-1/messages"
    ).mock(return_value=httpx.Response(201, json={"data": {"message_id": "m9"}}))

    await client.send_with_attachments(
        in_reply_to="m1",
        to="alice@example.com",
        subject="Re: hi",
        text_body="see attached",
        attachment_paths=[str(f)],
    )
    assert route.called
    req = route.calls.last.request
    import json as _json
    parsed = _json.loads(req.content.decode())
    assert parsed["to"] == ["alice@example.com"]
    assert parsed["subject"] == "Re: hi"
    assert parsed["in_reply_to"] == "m1"
    assert parsed["references"] == ["m1"]
    assert len(parsed["attachments"]) == 1
    att = parsed["attachments"][0]
    assert att["filename"] == "doc.txt"
    import base64 as _b64
    assert _b64.b64decode(att["data"]) == b"hello"


@pytest.mark.asyncio
@respx.mock
async def test_send_with_attachments_blocked_by_allowlist(restricted_client, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_bytes(b"x")
    with pytest.raises(DeadsimpleError):
        await restricted_client.send_with_attachments(
            in_reply_to="m1",
            to="evil@evil.com",
            subject="re",
            text_body="x",
            attachment_paths=[str(f)],
        )
```

- [ ] **Step 2: Verify tests fail**

Run: `pytest tests/test_deadsimple.py -v -k "send_reply or send_with_attachments"`
Expected: `AttributeError` on the new methods.

- [ ] **Step 3: Implement** — append:

```python
    def _check_recipients_allowed(self, *recipients: str | None) -> None:
        if not self.restrict_outbound or not self.allowed_recipients:
            return
        flat: list[str] = []
        for r in recipients:
            if not r:
                continue
            flat.extend(addr.strip() for addr in r.split(",") if addr.strip())
        blocked = [r for r in flat if not any(a in r for a in self.allowed_recipients)]
        if blocked:
            raise DeadsimpleError(
                f"Outbound email blocked: recipient(s) {blocked} not in allowlist "
                f"({self.allowed_recipients}). Set EMAIL_RESTRICT_OUTBOUND=false to disable."
            )

    async def send_reply(
        self, *, in_reply_to: str, to: str, text_body: str
    ) -> dict[str, Any]:
        """Threaded reply, server auto-sets In-Reply-To/References. No attachments supported."""
        self._check_recipients_allowed(to)
        return await self._request(
            "POST",
            f"/v1/inboxes/{self.inbox_id}/messages/{in_reply_to}/reply",
            json_body={"text_body": text_body},
            idempotency_key=f"reply-{in_reply_to}",
        )

    async def send_with_attachments(
        self,
        *,
        in_reply_to: str,
        to: str,
        subject: str,
        text_body: str,
        attachment_paths: list[str],
    ) -> dict[str, Any]:
        """Send via /messages with explicit threading + inline base64 attachments."""
        self._check_recipients_allowed(to)
        atts: list[dict[str, Any]] = []
        for p in attachment_paths:
            path = Path(p)
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            atts.append({
                "filename": path.name,
                "content_type": _guess_content_type(path.name),
                "data": data,
            })
        body = {
            "to": [to],
            "subject": subject,
            "text_body": text_body,
            "in_reply_to": in_reply_to,
            "references": [in_reply_to],
            "attachments": atts,
        }
        return await self._request(
            "POST",
            f"/v1/inboxes/{self.inbox_id}/messages",
            json_body=body,
            idempotency_key=f"send-attach-{in_reply_to}",
        )
```

Also add at the bottom of the file:

```python
def _guess_content_type(filename: str) -> str:
    import mimetypes
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"
```

- [ ] **Step 4: Verify tests pass**

Run: `pytest tests/test_deadsimple.py -v`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channels/deadsimple.py tests/test_deadsimple.py
git commit -m "feat(email): add send_reply + send_with_attachments to DeadsimpleClient"
```

---

## Task 6: Swap `EmailChannelConfig` fields

**Files:**
- Modify: `src/config.py`

Replace Gmail fields with deadsimple fields. Done as a single edit, no tests of its own — covered by config tests in Task 11 (the run.py wiring) and by all channel tests downstream.

- [ ] **Step 1: Edit `src/config.py`**

Replace the `EmailChannelConfig` dataclass (currently lines 26–34) with:

```python
@dataclass
class EmailChannelConfig:
    enabled: bool = False
    api_key: str = ""
    inbox_id: str = ""
    api_base: str = "https://api.deadsimple.email"
    poll_interval_sec: int = 60
    allowed_senders: list[str] = field(default_factory=list)
    restrict_outbound: bool = True
    attachment_dir: str = "/tmp/attachments"
    state_file: Path = Path("./context/email_state.json")
    spam_score_threshold: float = 5.0
```

Make sure `from pathlib import Path` is at the top of the file (it already is — line 3).

- [ ] **Step 2: Run the existing test_config tests to confirm we didn't break unrelated configs**

Run: `pytest tests/test_config.py -v`
Expected: pass (the existing tests don't reference the removed Gmail fields, but verify).

- [ ] **Step 3: Commit**

```bash
git add src/config.py
git commit -m "refactor(email): swap EmailChannelConfig fields for deadsimple"
```

---

## Task 7: Rewrite `EmailChannel` — constructor + startup

**Files:**
- Modify: `src/channels/email.py`
- Modify: `tests/test_email_channel.py`

Replace the entire `EmailChannel` with one that holds a `DeadsimpleClient` and an `EmailState`. Tests are rewritten from scratch — mocks change from `gmail.build_service`/`MagicMock` to `DeadsimpleClient` (mocked at the client level, not HTTP level).

- [ ] **Step 1: Replace `tests/test_email_channel.py` constructor/startup tests**

Open `tests/test_email_channel.py` and **replace the entire file** with:

```python
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.email import EmailChannel
from src.channels.deadsimple import DeadsimpleError
from src.config import EmailChannelConfig


@pytest.fixture
def email_config(tmp_path):
    return EmailChannelConfig(
        enabled=True,
        api_key="dse_test",
        inbox_id="inbox-uuid-1",
        api_base="https://api.deadsimple.email",
        poll_interval_sec=1,
        allowed_senders=["alice@example.com"],
        restrict_outbound=True,
        attachment_dir=str(tmp_path / "attachments"),
        state_file=tmp_path / "email_state.json",
        spam_score_threshold=5.0,
    )


@pytest.fixture
def in_queue():
    return asyncio.Queue()


def _make_channel(in_queue, config, client: AsyncMock | None = None):
    """Construct the channel with the deadsimple client patched out."""
    mock_client = client or AsyncMock()
    with patch("src.channels.email.DeadsimpleClient", return_value=mock_client):
        ch = EmailChannel(in_queue, config)
    return ch, mock_client


def test_constructor(email_config, in_queue):
    ch, _ = _make_channel(in_queue, email_config)
    assert ch.in_queue is in_queue
    assert ch.config is email_config
    assert ch.client is not None
    assert ch.poll_interval == 1
    assert ch.allowed_senders == ["alice@example.com"]
    assert ch.attachment_dir == email_config.attachment_dir
    # State starts blank
    assert ch.state.watermark_created_at is None


@pytest.mark.asyncio
async def test_start_validates_inbox_then_initializes_watermark(email_config, in_queue):
    client = AsyncMock()
    client.validate_inbox.return_value = {"data": {"inbox_id": "inbox-uuid-1", "email": "bot@deadsimple.email"}}
    client.list_messages.return_value = {"data": [], "next_cursor": None}

    ch, _ = _make_channel(in_queue, email_config, client=client)

    # Run one poll cycle then cancel.
    async def runner():
        task = asyncio.create_task(ch.start())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await runner()

    client.validate_inbox.assert_awaited_once()
    # Watermark file created with a non-empty timestamp.
    assert email_config.state_file.exists()
    saved = json.loads(email_config.state_file.read_text())
    assert saved["watermark_created_at"] is not None


@pytest.mark.asyncio
async def test_start_returns_early_on_inbox_validation_failure(email_config, in_queue, caplog):
    client = AsyncMock()
    client.validate_inbox.side_effect = DeadsimpleError("404: inbox not found")

    ch, _ = _make_channel(in_queue, email_config, client=client)
    await ch.start()  # returns without raising

    # No watermark file written, no list call made.
    client.list_messages.assert_not_called()
```

- [ ] **Step 2: Replace the entire `src/channels/email.py`**

Write the new file. The content below is the full replacement:

```python
"""Email channel — polls deadsimple.email for new messages, queues IncomingMessage,
sends replies into the same thread."""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.channels._attachments import (
    _normalize_unicode_whitespace,
    _validate_attachment_metadata,
)
from src.channels._email_state import EmailState
from src.channels.base import IncomingMessage, OutgoingMessage
from src.channels.deadsimple import DeadsimpleClient, DeadsimpleError
from src.config import EmailChannelConfig

logger = logging.getLogger(__name__)


class EmailChannel:
    def __init__(self, in_queue: asyncio.Queue, config: EmailChannelConfig):
        self.in_queue = in_queue
        self.config = config
        self.client = DeadsimpleClient(
            api_key=config.api_key,
            api_base=config.api_base,
            inbox_id=config.inbox_id,
            allowed_recipients=config.allowed_senders,
            restrict_outbound=config.restrict_outbound,
        )
        self.poll_interval = config.poll_interval_sec
        self.allowed_senders = config.allowed_senders
        self.attachment_dir = config.attachment_dir
        self.spam_score_threshold = config.spam_score_threshold
        self.state = EmailState.load(config.state_file)

    async def start(self) -> None:
        """Validate inbox, initialize watermark if needed, enter polling loop."""
        try:
            inbox = await self.client.validate_inbox()
        except DeadsimpleError as e:
            logger.error("Email channel failed to start (invalid inbox): %s", e)
            return

        email_addr = (inbox.get("data") or inbox).get("email", "<unknown>")
        logger.info("Email channel started, inbox=%s, polling every %ds",
                    email_addr, self.poll_interval)

        if self.state.watermark_created_at is None:
            self.state.set_watermark(datetime.now(timezone.utc), "")
            self.state.save()

        await self._poll_loop()

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._poll_once()
            except Exception:
                logger.exception("Error during email poll")
            await asyncio.sleep(self.poll_interval)

    async def _poll_once(self) -> None:
        """Stubbed until Task 8."""
        return

    async def send(self, msg: OutgoingMessage) -> None:
        """Stubbed until Task 10."""
        return
```

- [ ] **Step 3: Verify the new tests pass**

Run: `pytest tests/test_email_channel.py -v`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add src/channels/email.py tests/test_email_channel.py
git commit -m "refactor(email): rewrite EmailChannel skeleton on DeadsimpleClient"
```

---

## Task 8: Polling loop — pagination walk + watermark advance

**Files:**
- Modify: `src/channels/email.py`
- Modify: `tests/test_email_channel.py`

Implement `_poll_once` end-to-end except for attachment downloads (Task 9). It walks pages, filters by direction/spam/allowlist, fetches detail per inbound message, builds `IncomingMessage`, advances the watermark.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_email_channel.py`:

```python
def _msg(message_id, *, ts, direction="inbound", from_email="alice@example.com",
         subject="hi", thread_id="t1", is_spam=False, spam_score=0.0):
    return {
        "message_id": message_id,
        "thread_id": thread_id,
        "direction": direction,
        "from_email": from_email,
        "subject": subject,
        "is_spam": is_spam,
        "spam_score": spam_score,
        "created_at": ts,
    }


def _detail(message_id, *, text_body="hi body", thread_id="t1", subject="hi",
            from_email="alice@example.com", attachments=None):
    return {
        "message_id": message_id,
        "thread_id": thread_id,
        "from_email": from_email,
        "subject": subject,
        "text_body": text_body,
        "html_body": "",
        "attachments": attachments or [],
        "created_at": "2026-05-14T15:31:00Z",
        "direction": "inbound",
        "is_spam": False, "spam_score": 0.0,
    }


@pytest.mark.asyncio
async def test_poll_once_skips_outbound_messages(email_config, in_queue):
    client = AsyncMock()
    client.list_messages.return_value = {
        "data": [
            _msg("m1", ts="2026-05-14T15:31:00Z", direction="outbound"),
        ],
        "next_cursor": None,
    }
    ch, _ = _make_channel(in_queue, email_config, client=client)
    # Pretend startup ran:
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()
    assert in_queue.empty()
    client.get_message.assert_not_called()


@pytest.mark.asyncio
async def test_poll_once_drops_spam(email_config, in_queue):
    client = AsyncMock()
    client.list_messages.return_value = {
        "data": [
            _msg("m1", ts="2026-05-14T15:31:00Z", is_spam=True),
            _msg("m2", ts="2026-05-14T15:32:00Z", spam_score=6.0),
        ],
        "next_cursor": None,
    }
    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()
    assert in_queue.empty()


@pytest.mark.asyncio
async def test_poll_once_drops_disallowed_sender(email_config, in_queue):
    client = AsyncMock()
    client.list_messages.return_value = {
        "data": [
            _msg("m1", ts="2026-05-14T15:31:00Z", from_email="stranger@nope.com"),
        ],
        "next_cursor": None,
    }
    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()
    assert in_queue.empty()


@pytest.mark.asyncio
async def test_poll_once_queues_inbound_and_advances_watermark(email_config, in_queue):
    client = AsyncMock()
    client.list_messages.return_value = {
        "data": [
            _msg("m2", ts="2026-05-14T15:32:00Z"),
            _msg("m1", ts="2026-05-14T15:31:00Z"),
        ],
        "next_cursor": None,
    }
    client.get_message.side_effect = lambda mid: _detail(mid, text_body=f"body of {mid}")

    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()

    # Two queued in chronological order.
    first = in_queue.get_nowait()
    second = in_queue.get_nowait()
    assert "body of m1" in first.content   # m1 (older) first
    assert "body of m2" in second.content  # m2 second
    assert first.session_id == "t1"
    assert first.channel == "email"
    assert first.reply_address["in_reply_to"] == "m1"
    assert first.reply_address["to"] == "alice@example.com"
    assert first.reply_address["subject"] == "Re: hi"
    # Watermark advanced to the newest message.
    assert ch.state.watermark_message_id == "m2"


@pytest.mark.asyncio
async def test_poll_once_walks_pages_until_watermark(email_config, in_queue):
    """Pagination terminates the moment we cross the watermark."""
    client = AsyncMock()
    # First page has m4, m3, m2; second would have m1 (already seen).
    page1 = {
        "data": [
            _msg("m4", ts="2026-05-14T15:34:00Z"),
            _msg("m3", ts="2026-05-14T15:33:00Z"),
            _msg("m2", ts="2026-05-14T15:32:00Z"),
        ],
        "next_cursor": "cur-1",
    }
    page2 = {
        "data": [
            _msg("m1", ts="2026-05-14T15:31:00Z"),  # at watermark -- stop here
        ],
        "next_cursor": None,
    }
    client.list_messages.side_effect = [page1, page2]
    client.get_message.side_effect = lambda mid: _detail(mid, text_body=f"body of {mid}")

    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 31, 0, tzinfo=timezone.utc), "m1")

    await ch._poll_once()

    queued = [in_queue.get_nowait() for _ in range(in_queue.qsize())]
    assert [m.reply_address["in_reply_to"] for m in queued] == ["m2", "m3", "m4"]
    # Walked two pages.
    assert client.list_messages.call_count == 2


@pytest.mark.asyncio
async def test_poll_once_does_not_advance_watermark_on_empty_batch(email_config, in_queue):
    client = AsyncMock()
    client.list_messages.return_value = {"data": [], "next_cursor": None}

    ch, _ = _make_channel(in_queue, email_config, client=client)
    original = datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc)
    ch.state.set_watermark(original, "msg-old")

    await ch._poll_once()
    assert ch.state.watermark_created_at == original
    assert ch.state.watermark_message_id == "msg-old"
```

- [ ] **Step 2: Verify tests fail**

Run: `pytest tests/test_email_channel.py -v`
Expected: 6 new tests fail (the previous 3 still pass).

- [ ] **Step 3: Implement `_poll_once`**

Replace the stub `_poll_once` in `src/channels/email.py` with the following. Also add the helper imports/methods shown.

At the top of the file, replace this import line:

```python
from datetime import datetime, timezone
```

with:

```python
from datetime import datetime, timezone

_RE_PREFIX_RE = ("re:", "fw:", "fwd:")
```

Replace `_poll_once` and add helpers (insert before the `send` method):

```python
    async def _poll_once(self) -> None:
        """Walk pages newest-first until we hit the watermark, process new inbound."""
        cursor: str | None = None
        new_messages: list[dict[str, Any]] = []
        max_seen: tuple[datetime, str] | None = None

        while True:
            page = await self.client.list_messages(limit=50, cursor=cursor)
            data = page.get("data", [])
            stop = False
            for m in data:
                ts = self._parse_ts(m.get("created_at", ""))
                if ts is None:
                    continue
                mid = m.get("message_id", "")
                if not self.state.is_after_watermark(ts, mid):
                    stop = True
                    break
                if max_seen is None or (ts, mid) > max_seen:
                    max_seen = (ts, mid)
                new_messages.append(m)
            if stop or not page.get("next_cursor"):
                break
            cursor = page["next_cursor"]

        # Oldest first into the queue.
        for summary in reversed(new_messages):
            await self._handle_summary(summary)

        if max_seen is not None:
            self.state.set_watermark(*max_seen)
            self.state.save()

    async def _handle_summary(self, summary: dict[str, Any]) -> None:
        if summary.get("direction") != "inbound":
            return
        if summary.get("is_spam") or float(summary.get("spam_score") or 0) >= self.spam_score_threshold:
            logger.info("Dropping spam message %s (score=%s)",
                         summary.get("message_id"), summary.get("spam_score"))
            return
        sender = summary.get("from_email", "")
        if self.allowed_senders and not any(a in sender for a in self.allowed_senders):
            logger.info("Skipping email from %s (not in allowed_senders)", sender)
            return

        try:
            detail = await self.client.get_message(summary["message_id"])
        except DeadsimpleError:
            logger.exception("Failed to fetch detail for %s", summary.get("message_id"))
            return

        body = detail.get("text_body") or self._strip_html(detail.get("html_body", "")) or ""
        content = f"[channel: email, from: {sender}]\n{body}" if sender else body

        subject = detail.get("subject", "") or ""
        reply_subject = subject if subject.lower().startswith(_RE_PREFIX_RE) else f"Re: {subject}"

        incoming = IncomingMessage(
            content=content,
            channel="email",
            session_id=detail.get("thread_id", ""),
            reply_address={
                "to": sender,
                "subject": reply_subject,
                "in_reply_to": detail["message_id"],
            },
            attachments=None,  # Task 9 fills this in.
        )
        await self.in_queue.put(incoming)
        logger.info("Queued email from %s (thread %s): %s",
                    sender, incoming.session_id, subject)

    @staticmethod
    def _parse_ts(s: str) -> datetime | None:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _strip_html(html: str) -> str:
        """Crude HTML-to-text fallback used only when text_body is empty."""
        import re as _re
        return _re.sub(r"<[^>]+>", "", html).strip()
```

- [ ] **Step 4: Verify tests pass**

Run: `pytest tests/test_email_channel.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channels/email.py tests/test_email_channel.py
git commit -m "feat(email): implement polling loop with watermark pagination"
```

---

## Task 9: Inbound attachment download

**Files:**
- Modify: `src/channels/email.py`
- Modify: `tests/test_email_channel.py`

Wire the attachment manifest into `IncomingMessage`. Reuse `_validate_attachment_metadata` and `_normalize_unicode_whitespace` from `_attachments.py`. A failed download for one attachment must not block the message — log and skip.

- [ ] **Step 1: Write the failing tests** — append:

```python
@pytest.mark.asyncio
async def test_poll_once_downloads_attachments(email_config, in_queue, tmp_path):
    client = AsyncMock()
    client.list_messages.return_value = {
        "data": [_msg("m1", ts="2026-05-14T15:31:00Z")],
        "next_cursor": None,
    }
    client.get_message.return_value = _detail(
        "m1", thread_id="t1",
        attachments=[
            {"attachment_id": "a1", "filename": "report.pdf",
             "content_type": "application/pdf", "size": 1024},
        ],
    )
    async def fake_download(message_id, attachment_id, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"PDF")
    client.download_attachment.side_effect = fake_download

    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()
    incoming = in_queue.get_nowait()
    assert incoming.attachments is not None and len(incoming.attachments) == 1
    att = incoming.attachments[0]
    assert att["filename"] == "report.pdf"
    assert att["mime_type"] == "application/pdf"
    assert att["size"] == 3   # actual on-disk bytes
    assert Path(att["path"]).read_bytes() == b"PDF"
    # Body content lists the attachment.
    assert "report.pdf" in incoming.content


@pytest.mark.asyncio
async def test_poll_once_skips_failed_attachment_but_keeps_message(email_config, in_queue):
    client = AsyncMock()
    client.list_messages.return_value = {
        "data": [_msg("m1", ts="2026-05-14T15:31:00Z")],
        "next_cursor": None,
    }
    client.get_message.return_value = _detail("m1", attachments=[
        {"attachment_id": "a1", "filename": "broken.pdf",
         "content_type": "application/pdf", "size": 1024},
    ])
    client.download_attachment.side_effect = DeadsimpleError("expired URL")

    ch, _ = _make_channel(in_queue, email_config, client=client)
    ch.state.set_watermark(datetime(2026, 5, 14, 15, 0, 0, tzinfo=timezone.utc), "")

    await ch._poll_once()
    incoming = in_queue.get_nowait()
    assert incoming.attachments is None  # download failed → no manifest entry
```

- [ ] **Step 2: Verify the new tests fail**

Run: `pytest tests/test_email_channel.py -v -k "attachment"`
Expected: both fail (the current channel sets attachments=None unconditionally).

- [ ] **Step 3: Add the attachment helper and wire it in**

In `src/channels/email.py`, replace `_handle_summary` with the version below (just the diff inside the function, leaving signature and the early-returns intact). Also add `_process_attachments` as a new method just below it.

Replace the body of `_handle_summary` from the `try: detail = ...` line onwards with:

```python
        try:
            detail = await self.client.get_message(summary["message_id"])
        except DeadsimpleError:
            logger.exception("Failed to fetch detail for %s", summary.get("message_id"))
            return

        thread_id = detail.get("thread_id", "")
        attachments = await self._process_attachments(detail, thread_id)

        body = detail.get("text_body") or self._strip_html(detail.get("html_body", "")) or ""
        content = f"[channel: email, from: {sender}]\n{body}" if sender else body
        if attachments:
            content += "\n\nAttachments:\n"
            for att in attachments:
                size_kb = max(att["size"] // 1024, 1)
                content += f"- {att['filename']} ({att['mime_type']}, {size_kb}KB) -> {att['path']}\n"

        subject = detail.get("subject", "") or ""
        reply_subject = subject if subject.lower().startswith(_RE_PREFIX_RE) else f"Re: {subject}"

        incoming = IncomingMessage(
            content=content,
            channel="email",
            session_id=thread_id,
            reply_address={
                "to": sender,
                "subject": reply_subject,
                "in_reply_to": detail["message_id"],
            },
            attachments=attachments,
        )
        await self.in_queue.put(incoming)
        logger.info("Queued email from %s (thread %s): %s",
                    sender, incoming.session_id, subject)
```

Add this new method below `_handle_summary`:

```python
    async def _process_attachments(
        self, detail: dict[str, Any], thread_id: str
    ) -> list[dict] | None:
        raw = detail.get("attachments") or []
        if not raw:
            return None
        out_dir = Path(self.attachment_dir).resolve() / thread_id
        out_dir.mkdir(parents=True, exist_ok=True)

        manifest: list[dict] = []
        for att in raw:
            att_id = att.get("attachment_id")
            fname_raw = att.get("filename", "")
            if not att_id or not fname_raw:
                continue
            fname = _normalize_unicode_whitespace(fname_raw)
            mime = att.get("content_type") or "application/octet-stream"
            declared_size = int(att.get("size") or 0)
            reason = _validate_attachment_metadata(mime, declared_size)
            if reason:
                logger.warning("Dropping email attachment %s: %s", fname, reason)
                continue
            dest = out_dir / fname
            try:
                await self.client.download_attachment(detail["message_id"], att_id, dest)
            except DeadsimpleError:
                logger.exception("Failed to download attachment %s", fname)
                continue
            if not dest.is_file():
                continue
            manifest.append({
                "filename": fname,
                "path": str(dest),
                "mime_type": mime,
                "size": dest.stat().st_size,
            })
        return manifest or None
```

- [ ] **Step 4: Verify tests pass**

Run: `pytest tests/test_email_channel.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channels/email.py tests/test_email_channel.py
git commit -m "feat(email): download inbound attachments via deadsimple signed URLs"
```

---

## Task 10: `EmailChannel.send()` — route between reply and send-with-attachments

**Files:**
- Modify: `src/channels/email.py`
- Modify: `tests/test_email_channel.py`

- [ ] **Step 1: Write the failing tests** — append:

```python
from src.channels.base import OutgoingMessage


def _outgoing(content, *, reply_address, attachments=None, final=True):
    return OutgoingMessage(
        content=content,
        channel="email",
        session_id="t1",
        reply_address=reply_address,
        attachments=attachments,
        final=final,
    )


@pytest.mark.asyncio
async def test_send_skips_streaming_deltas(email_config, in_queue):
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    msg = _outgoing("partial", reply_address={"to": "a@x.com", "subject": "re", "in_reply_to": "m1"}, final=False)
    await ch.send(msg)
    client.send_reply.assert_not_called()
    client.send_with_attachments.assert_not_called()


@pytest.mark.asyncio
async def test_send_uses_reply_endpoint_when_no_attachments(email_config, in_queue):
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    msg = _outgoing(
        "Hi back",
        reply_address={"to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"},
    )
    await ch.send(msg)
    client.send_reply.assert_awaited_once_with(
        in_reply_to="m1", to="alice@example.com", text_body="Hi back"
    )
    client.send_with_attachments.assert_not_called()


@pytest.mark.asyncio
async def test_send_uses_messages_endpoint_when_attachments_present(email_config, in_queue, tmp_path):
    client = AsyncMock()
    ch, _ = _make_channel(in_queue, email_config, client=client)
    f = tmp_path / "doc.txt"
    f.write_bytes(b"x")
    msg = _outgoing(
        "see attached",
        reply_address={"to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"},
        attachments=[{"filename": "doc.txt", "path": str(f), "mime_type": "text/plain", "size": 1}],
    )
    await ch.send(msg)
    client.send_with_attachments.assert_awaited_once_with(
        in_reply_to="m1",
        to="alice@example.com",
        subject="Re: hi",
        text_body="see attached",
        attachment_paths=[str(f)],
    )
    client.send_reply.assert_not_called()


@pytest.mark.asyncio
async def test_send_logs_and_returns_on_deadsimple_error(email_config, in_queue, caplog):
    client = AsyncMock()
    client.send_reply.side_effect = DeadsimpleError("rate limited")
    ch, _ = _make_channel(in_queue, email_config, client=client)
    msg = _outgoing(
        "hi",
        reply_address={"to": "alice@example.com", "subject": "Re: hi", "in_reply_to": "m1"},
    )
    await ch.send(msg)  # does not raise
```

- [ ] **Step 2: Verify tests fail**

Run: `pytest tests/test_email_channel.py -v -k "send"`
Expected: 4 failures (current `send` is a stub).

- [ ] **Step 3: Implement `send`** — replace the stub `send` with:

```python
    async def send(self, msg: OutgoingMessage) -> None:
        """Send a reply via deadsimple. Routes to /reply for text-only, /messages when attaching."""
        if not msg.final or not msg.content:
            return
        in_reply_to = msg.reply_address.get("in_reply_to")
        to = msg.reply_address.get("to")
        subject = msg.reply_address.get("subject")
        if not in_reply_to or not to:
            logger.error("Email send missing in_reply_to or to (got %s)", msg.reply_address)
            return

        attachments = msg.attachments or []
        try:
            if attachments:
                paths = [a["path"] for a in attachments if a.get("path")]
                await self.client.send_with_attachments(
                    in_reply_to=in_reply_to,
                    to=to,
                    subject=subject or "",
                    text_body=msg.content,
                    attachment_paths=paths,
                )
            else:
                await self.client.send_reply(
                    in_reply_to=in_reply_to,
                    to=to,
                    text_body=msg.content,
                )
        except DeadsimpleError:
            logger.exception("Failed to send reply for thread %s", msg.session_id)
```

- [ ] **Step 4: Verify tests pass**

Run: `pytest tests/test_email_channel.py -v`
Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channels/email.py tests/test_email_channel.py
git commit -m "feat(email): send via /reply or /messages depending on attachments"
```

---

## Task 11: `run.py` wiring, delete Gmail code, update `.env.example`

**Files:**
- Modify: `run.py`
- Delete: `src/channels/gmail.py`
- Delete: `tests/test_gmail.py`
- Delete: `docs/gmail-setup.md`
- Modify: `.env.example`
- Modify: `requirements.txt`

- [ ] **Step 1: Update `run.py`**

Replace the email-channel block (currently lines 518–531) with:

```python
    # Email channel (conditional)
    email_config = EmailChannelConfig(
        enabled=os.environ.get("EMAIL_ENABLED", "false").lower() == "true",
        api_key=os.environ.get("DEADSIMPLE_API_KEY", ""),
        inbox_id=os.environ.get("DEADSIMPLE_INBOX_ID", ""),
        api_base=os.environ.get("DEADSIMPLE_API_BASE", "https://api.deadsimple.email"),
        poll_interval_sec=int(os.environ.get("EMAIL_POLL_INTERVAL", "60")),
        allowed_senders=[s.strip() for s in os.environ.get("EMAIL_ALLOWED_SENDERS", "").split(",") if s.strip()],
        restrict_outbound=os.environ.get("EMAIL_RESTRICT_OUTBOUND", "true").lower() == "true",
        attachment_dir=os.environ.get("EMAIL_ATTACHMENT_DIR", "/tmp/attachments"),
        state_file=Path(os.environ.get("EMAIL_STATE_FILE", "./context/email_state.json")),
        spam_score_threshold=float(os.environ.get("EMAIL_SPAM_SCORE_THRESHOLD", "5.0")),
    )
    if email_config.enabled:
        if not email_config.api_key or not email_config.inbox_id:
            logger.error("EMAIL_ENABLED=true but DEADSIMPLE_API_KEY or DEADSIMPLE_INBOX_ID is unset; skipping email channel")
        else:
            email_channel = EmailChannel(in_queue, email_config)
            channels["email"] = email_channel
            logger.info("Email channel enabled for inbox %s (poll every %ds)",
                        email_config.inbox_id, email_config.poll_interval_sec)
```

If `from pathlib import Path` is not already imported at the top of `run.py`, add it.

- [ ] **Step 2: Delete Gmail files**

```bash
git rm src/channels/gmail.py tests/test_gmail.py docs/gmail-setup.md
```

- [ ] **Step 3: Update `requirements.txt`**

Remove these two lines:

```
google-auth
google-api-python-client
```

(`respx` was already added in Task 2. Keep it.)

- [ ] **Step 4: Update `.env.example`**

Open `.env.example`. Remove any `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_DELEGATED_USER`, and `EMAIL_PROCESSED_LABEL` entries. Add (under the existing email block, or near it):

```
# Email channel (deadsimple.email)
EMAIL_ENABLED=false
DEADSIMPLE_API_KEY=dse_your_api_key_here
DEADSIMPLE_INBOX_ID=00000000-0000-0000-0000-000000000000
# Override API base (rarely needed)
# DEADSIMPLE_API_BASE=https://api.deadsimple.email
EMAIL_POLL_INTERVAL=60
EMAIL_ALLOWED_SENDERS=
EMAIL_RESTRICT_OUTBOUND=true
EMAIL_ATTACHMENT_DIR=/tmp/attachments
EMAIL_STATE_FILE=./context/email_state.json
EMAIL_SPAM_SCORE_THRESHOLD=5.0
```

If `.env.example` doesn't currently exist or doesn't have an email block, just add the block above near other channel configs.

- [ ] **Step 5: Run the full test suite to catch any stray Gmail references**

Run: `pytest tests/ -q`
Expected: all tests pass. If anything imports from `src.channels.gmail`, fix the import (most likely in `tests/test_email_channel.py` — confirm the new file doesn't reference `gmail`).

- [ ] **Step 6: Run a quick import smoke check**

Run: `python -c "import run; print('ok')"`
Expected: prints `ok` with no traceback.

- [ ] **Step 7: Commit**

```bash
git add run.py requirements.txt .env.example
git commit -m "feat(email): wire deadsimple env vars, drop Gmail dependencies"
```

(The `git rm` from Step 2 stages the deletions; they go into this commit.)

---

## Task 12: Documentation update

**Files:**
- Modify: `docs/architecture.md`
- Modify: `README.md`
- Create: `docs/deadsimple-setup.md` (or extend README — choose based on existing structure)

- [ ] **Step 1: Update `docs/architecture.md`**

Locate the Channels section. Replace the email row/paragraph describing Gmail with one describing deadsimple:

> **Email (`email.py`):** Polls deadsimple.email via HTTP every 60s. Session ID is the deadsimple thread UUID. Processed-state is tracked by a `(created_at, message_id)` watermark in `context/email_state.json`. Sends replies via `/messages/{id}/reply` (text-only) or `/messages` with explicit threading headers + base64 attachments.

Add a changelog entry at the bottom:

```
- 2026-05-14 — Replaced Gmail (`gmail.py`) with deadsimple.email (`deadsimple.py`) as the email transport. The `EmailChannel` interface is unchanged; configuration swapped from `GOOGLE_*` to `DEADSIMPLE_*`. Spec: `docs/superpowers/specs/2026-05-14-deadsimple-email-channel-design.md`.
```

If the file has an ADR table, add:

```
- ADR: Use deadsimple.email native `thread_id` as session_id (no header-walking). Drop server-side processed-state labels in favor of a local watermark file — deadsimple has no label-write endpoint.
```

- [ ] **Step 2: Update `README.md`**

Find the email setup section (or the features list). Replace any reference to Gmail/service-account with:

```markdown
### Email (optional)

The email channel uses [deadsimple.email](https://deadsimple.email):

1. Create an account and an inbox at deadsimple.email.
2. Generate an API key (with `read,write` permissions).
3. Set in `.env`:
   ```
   EMAIL_ENABLED=true
   DEADSIMPLE_API_KEY=dse_...
   DEADSIMPLE_INBOX_ID=<inbox-uuid>
   EMAIL_ALLOWED_SENDERS=you@example.com,teammate@example.com
   ```
4. Restart. The first poll skips historical mail; new messages appear within `EMAIL_POLL_INTERVAL` seconds.
```

If the README has a test count, recompute it from `pytest --collect-only -q | tail -1` and update.

- [ ] **Step 3: Run docs sanity check**

```bash
grep -r "google-api-python-client\|GOOGLE_SERVICE_ACCOUNT\|gog gmail" README.md docs/ 2>/dev/null
```

Expected: only matches inside `docs/superpowers/specs/` and `docs/superpowers/plans/` (historical specs/plans we don't rewrite) — nothing in `README.md` or `docs/architecture.md`.

- [ ] **Step 4: Commit**

```bash
git add docs/architecture.md README.md
git commit -m "docs: replace Gmail references with deadsimple.email setup"
```

---

## Final verification

- [ ] **Run the full test suite one more time**

Run: `pytest tests/ -q`
Expected: all green, including:
- `tests/test_email_state.py` (5)
- `tests/test_deadsimple.py` (13)
- `tests/test_email_channel.py` (15)

- [ ] **Confirm no remaining references to Gmail in production code**

Run: `grep -rn "from src.channels.gmail\|src\.channels\.gmail\|google\.oauth2\|googleapiclient" src/ run.py 2>/dev/null`
Expected: no matches.

- [ ] **Confirm the cleaned-up dependency tree**

Run: `pip check`
Expected: no broken dependencies.

---

## Out of Scope (do not implement here)

- Webhook-based inbound delivery (`POST /v1/webhooks`).
- Custom-domain provisioning.
- Open/click tracking on outbound.
- Template-based sends.
- Multi-inbox support.
- Importing or migrating historical Gmail threads.
- HTML composition on outbound.
- Reply-all / forward.
