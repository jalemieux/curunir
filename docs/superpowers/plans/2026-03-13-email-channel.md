# Email Channel Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an email channel that polls Gmail via the `gog` CLI, processes inbound messages through the agent loop, and sends replies in the original thread.

**Architecture:** `EmailChannel` implements the existing `Channel` protocol (`start()` + `send()`). It shells out to `gog` for all Gmail operations. Config is hydrated from environment variables in `run.py` and passed as `EmailChannelConfig`. The channel pushes `IncomingMessage`s to the shared `in_queue`; outbound replies arrive via `send()` from the router.

**Tech Stack:** Python 3.12, asyncio, `gog` CLI (external binary), dataclasses

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/channels/base.py` | Modify | Add `attachments` field to `IncomingMessage` |
| `src/channels/gog.py` | Create | Thin wrapper for `gog` CLI commands (search, thread get, send, labels) |
| `src/channels/email.py` | Create | `EmailChannel` — poll loop, message construction, send+label |
| `src/config.py` | Modify | Add `EmailChannelConfig` dataclass |
| `run.py` | Modify | Wire up `EmailChannel` when `EMAIL_ENABLED=true` |
| `tests/test_gog.py` | Create | Tests for gog CLI wrapper |
| `tests/test_email_channel.py` | Create | Tests for `EmailChannel` |
| `tests/test_config.py` | Modify | Tests for `EmailChannelConfig` |

**Design note:** `gog.py` is split from `email.py` so the channel logic is testable without mocking subprocess internals everywhere. `gog.py` handles subprocess calls + JSON parsing; `email.py` handles channel protocol, polling, message construction.

---

## Chunk 1: Config and Base Message Changes

### Task 1: Add `attachments` field to `IncomingMessage`

**Files:**
- Modify: `src/channels/base.py:5-9`
- Test: `tests/test_channels.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_channels.py`:

```python
def test_incoming_message_attachments_default():
    msg = IncomingMessage(content="hello", channel="cli", session_id="cli", reply_address={})
    assert msg.attachments is None


def test_incoming_message_with_attachments():
    attachments = [{"filename": "report.pdf", "path": "/tmp/report.pdf", "mime_type": "application/pdf", "size": 1024}]
    msg = IncomingMessage(content="hello", channel="cli", session_id="cli", reply_address={}, attachments=attachments)
    assert msg.attachments == attachments
    assert msg.attachments[0]["filename"] == "report.pdf"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_channels.py::test_incoming_message_attachments_default tests/test_channels.py::test_incoming_message_with_attachments -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'attachments'`

- [ ] **Step 3: Add attachments field to IncomingMessage**

In `src/channels/base.py`, add after the `command` field:

```python
@dataclass
class IncomingMessage:
    content: str
    channel: str
    session_id: str
    reply_address: dict
    command: str | None = None
    attachments: list[dict] | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_channels.py -v`
Expected: ALL PASS (existing tests unaffected since `attachments` defaults to `None`)

- [ ] **Step 5: Commit**

```bash
git add src/channels/base.py tests/test_channels.py
git commit -m "feat: add attachments field to IncomingMessage"
```

---

### Task 2: Add `EmailChannelConfig` dataclass

**Files:**
- Modify: `src/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
from src.config import AgentConfig, EmailChannelConfig


def test_email_config_defaults():
    config = EmailChannelConfig()
    assert config.enabled is False
    assert config.account == ""
    assert config.poll_interval_sec == 60
    assert config.allowed_senders == []
    assert config.processed_label == "agent/processed"
    assert config.attachment_dir == "/tmp/attachments"


def test_email_config_custom():
    config = EmailChannelConfig(
        enabled=True,
        account="bot@example.com",
        poll_interval_sec=30,
        allowed_senders=["alice@example.com"],
        processed_label="custom/done",
        attachment_dir="/data/attachments",
    )
    assert config.enabled is True
    assert config.account == "bot@example.com"
    assert config.poll_interval_sec == 30
    assert config.allowed_senders == ["alice@example.com"]
    assert config.processed_label == "custom/done"
    assert config.attachment_dir == "/data/attachments"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py::test_email_config_defaults tests/test_config.py::test_email_config_custom -v`
Expected: FAIL with `ImportError: cannot import name 'EmailChannelConfig'`

- [ ] **Step 3: Implement EmailChannelConfig**

Add to `src/config.py`:

```python
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentConfig:
    model: str = "anthropic/claude-sonnet-4-20250514"
    max_iterations: int = 15
    identity_file: Path = Path("./context/identity.md")
    skills_dir: Path = Path("./skills")


@dataclass
class EmailChannelConfig:
    enabled: bool = False
    account: str = ""
    poll_interval_sec: int = 60
    allowed_senders: list[str] = field(default_factory=list)
    processed_label: str = "agent/processed"
    attachment_dir: str = "/tmp/attachments"
```

Note: the import changes from `from dataclasses import dataclass` to `from dataclasses import dataclass, field`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: add EmailChannelConfig dataclass"
```

---

## Chunk 2: Gog CLI Wrapper

### Task 3: Create `gog.py` — gog CLI wrapper

This module wraps all `gog` subprocess calls. Every function runs `gog` with `--json` where applicable, parses JSON output, and raises on failure. The email channel calls these functions instead of subprocess directly.

**Files:**
- Create: `src/channels/gog.py`
- Create: `tests/test_gog.py`

- [ ] **Step 1: Write the failing test for `check_installed()`**

Create `tests/test_gog.py`:

```python
import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from src.channels.gog import check_installed, GogError


def test_check_installed_success():
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        check_installed()  # should not raise


def test_check_installed_not_found():
    with patch("src.channels.gog.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(GogError, match="gog CLI is not installed"):
            check_installed()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_gog.py::test_check_installed_success tests/test_gog.py::test_check_installed_not_found -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `check_installed()`**

Create `src/channels/gog.py`:

```python
"""Thin wrapper around the gog CLI for Gmail operations."""

import json
import subprocess


class GogError(Exception):
    """Raised when a gog command fails."""


def check_installed() -> None:
    """Verify gog CLI is available. Raises GogError if not."""
    try:
        subprocess.run(["gog", "--version"], capture_output=True, check=False)
    except FileNotFoundError:
        raise GogError("gog CLI is not installed. Install it from https://github.com/jantari/gog")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_gog.py -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for `labels_list()` and `labels_create()`**

Add to `tests/test_gog.py`:

```python
from src.channels.gog import labels_list, labels_create


def test_labels_list_returns_parsed_json():
    labels_json = json.dumps([{"id": "Label_1", "name": "agent/processed"}])
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=labels_json)
        result = labels_list("bot@example.com")
    assert result == [{"id": "Label_1", "name": "agent/processed"}]
    mock_run.assert_called_once_with(
        ["gog", "gmail", "labels", "list", "--json", "--account", "bot@example.com"],
        capture_output=True, text=True, check=False,
    )


def test_labels_list_command_fails():
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="auth error")
        with pytest.raises(GogError, match="auth error"):
            labels_list("bot@example.com")


def test_labels_create():
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        labels_create("agent/processed", "bot@example.com")
    mock_run.assert_called_once_with(
        ["gog", "gmail", "labels", "create", "agent/processed", "--account", "bot@example.com"],
        capture_output=True, text=True, check=False,
    )
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `python -m pytest tests/test_gog.py::test_labels_list_returns_parsed_json tests/test_gog.py::test_labels_create -v`
Expected: FAIL with `ImportError`

- [ ] **Step 7: Implement `labels_list()` and `labels_create()`**

Add to `src/channels/gog.py`:

```python
def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Run a gog command, raising GogError on non-zero exit."""
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise GogError(result.stderr or f"gog exited with code {result.returncode}")
    return result


def _run_json(args: list[str]) -> list | dict:
    """Run a gog command and parse JSON output."""
    result = _run(args)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise GogError(f"Failed to parse gog JSON output: {e}")


def labels_list(account: str) -> list[dict]:
    """List all Gmail labels."""
    return _run_json(["gog", "gmail", "labels", "list", "--json", "--account", account])


def labels_create(label: str, account: str) -> None:
    """Create a Gmail label."""
    _run(["gog", "gmail", "labels", "create", label, "--account", account])
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m pytest tests/test_gog.py -v`
Expected: ALL PASS

- [ ] **Step 9: Write failing tests for `search()` and `thread_get()`**

Add to `tests/test_gog.py`:

```python
from src.channels.gog import search, thread_get


def test_search_returns_threads():
    threads_json = json.dumps([{"id": "thread_1", "snippet": "hello"}])
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=threads_json)
        result = search("-label:agent/processed", "bot@example.com", max_results=20)
    assert result == [{"id": "thread_1", "snippet": "hello"}]
    mock_run.assert_called_once_with(
        ["gog", "gmail", "search", "-label:agent/processed", "--json", "--max", "20", "--account", "bot@example.com"],
        capture_output=True, text=True, check=False,
    )


def test_search_empty_results():
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")
        result = search("-label:agent/processed", "bot@example.com")
    assert result == []


def test_thread_get():
    thread_json = json.dumps({"id": "thread_1", "messages": [{"id": "msg_1"}]})
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=thread_json)
        result = thread_get("thread_1", "bot@example.com")
    assert result == {"id": "thread_1", "messages": [{"id": "msg_1"}]}
    mock_run.assert_called_once_with(
        ["gog", "gmail", "thread", "get", "thread_1", "--json", "--account", "bot@example.com"],
        capture_output=True, text=True, check=False,
    )
```

- [ ] **Step 10: Run tests to verify they fail**

Run: `python -m pytest tests/test_gog.py::test_search_returns_threads tests/test_gog.py::test_thread_get -v`
Expected: FAIL with `ImportError`

- [ ] **Step 11: Implement `search()` and `thread_get()`**

Add to `src/channels/gog.py`:

```python
def search(query: str, account: str, max_results: int = 20) -> list[dict]:
    """Search Gmail threads matching a query."""
    return _run_json([
        "gog", "gmail", "search", query,
        "--json", "--max", str(max_results), "--account", account,
    ])


def thread_get(thread_id: str, account: str) -> dict:
    """Get a thread by ID."""
    return _run_json([
        "gog", "gmail", "thread", "get", thread_id,
        "--json", "--account", account,
    ])
```

- [ ] **Step 12: Run tests to verify they pass**

Run: `python -m pytest tests/test_gog.py -v`
Expected: ALL PASS

- [ ] **Step 13: Write failing tests for `thread_download_attachments()`, `send_reply()`, and `thread_modify()`**

Add to `tests/test_gog.py`:

```python
from src.channels.gog import thread_download_attachments, send_reply, thread_modify


def test_thread_download_attachments():
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        thread_download_attachments("thread_1", "/tmp/attachments/thread_1", "bot@example.com")
    mock_run.assert_called_once_with(
        ["gog", "gmail", "thread", "get", "thread_1", "--download", "--out-dir", "/tmp/attachments/thread_1", "--account", "bot@example.com"],
        capture_output=True, text=True, check=False,
    )


def test_send_reply():
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        send_reply(
            to="alice@example.com",
            subject="Re: Hello",
            body="Got it!",
            reply_to_message_id="msg_1",
            account="bot@example.com",
        )
    mock_run.assert_called_once_with(
        [
            "gog", "gmail", "send",
            "--reply-to-message-id", "msg_1",
            "--to", "alice@example.com",
            "--subject", "Re: Hello",
            "--body", "Got it!",
            "--account", "bot@example.com",
        ],
        capture_output=True, text=True, check=False,
    )


def test_thread_modify_add_label():
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        thread_modify("thread_1", add_label="agent/processed", account="bot@example.com")
    mock_run.assert_called_once_with(
        ["gog", "gmail", "thread", "modify", "thread_1", "--add", "agent/processed", "--account", "bot@example.com"],
        capture_output=True, text=True, check=False,
    )
```

- [ ] **Step 14: Run tests to verify they fail**

Run: `python -m pytest tests/test_gog.py::test_thread_download_attachments tests/test_gog.py::test_send_reply tests/test_gog.py::test_thread_modify_add_label -v`
Expected: FAIL with `ImportError`

- [ ] **Step 15: Implement remaining gog functions**

Add to `src/channels/gog.py`:

```python
def thread_download_attachments(thread_id: str, out_dir: str, account: str) -> None:
    """Download attachments from a thread to a directory."""
    _run([
        "gog", "gmail", "thread", "get", thread_id,
        "--download", "--out-dir", out_dir, "--account", account,
    ])


def send_reply(to: str, subject: str, body: str, reply_to_message_id: str, account: str) -> None:
    """Send a reply to a message."""
    _run([
        "gog", "gmail", "send",
        "--reply-to-message-id", reply_to_message_id,
        "--to", to,
        "--subject", subject,
        "--body", body,
        "--account", account,
    ])


def thread_modify(thread_id: str, add_label: str, account: str) -> None:
    """Modify a thread (add a label)."""
    _run([
        "gog", "gmail", "thread", "modify", thread_id,
        "--add", add_label, "--account", account,
    ])
```

- [ ] **Step 16: Run all gog tests**

Run: `python -m pytest tests/test_gog.py -v`
Expected: ALL PASS

- [ ] **Step 17: Write failing test for malformed JSON handling**

Add to `tests/test_gog.py`:

```python
def test_run_json_malformed():
    with patch("src.channels.gog.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")
        with pytest.raises(GogError, match="Failed to parse gog JSON output"):
            labels_list("bot@example.com")
```

- [ ] **Step 18: Run test to verify it passes** (already handled by `_run_json`)

Run: `python -m pytest tests/test_gog.py::test_run_json_malformed -v`
Expected: PASS

- [ ] **Step 19: Commit**

```bash
git add src/channels/gog.py tests/test_gog.py
git commit -m "feat: add gog CLI wrapper for Gmail operations"
```

---

## Chunk 3: EmailChannel Core

### Task 4: Create `EmailChannel` — startup and label bootstrap

**Files:**
- Create: `src/channels/email.py`
- Create: `tests/test_email_channel.py`

- [ ] **Step 1: Write failing tests for constructor and label bootstrap**

Create `tests/test_email_channel.py`:

```python
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from src.channels.email import EmailChannel
from src.config import EmailChannelConfig


@pytest.fixture
def email_config():
    return EmailChannelConfig(
        enabled=True,
        account="bot@example.com",
        poll_interval_sec=1,
        allowed_senders=["alice@example.com"],
        processed_label="agent/processed",
        attachment_dir="/tmp/attachments",
    )


@pytest.fixture
def in_queue():
    return asyncio.Queue()


def test_constructor(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)
    assert ch.in_queue is in_queue
    assert ch.config is email_config
    assert ch.account == "bot@example.com"
    assert ch.poll_interval == 1
    assert ch.allowed_senders == ["alice@example.com"]
    assert ch.processed_label == "agent/processed"
    assert ch.attachment_dir == "/tmp/attachments"
    assert ch.last_seen == {}


@pytest.mark.asyncio
async def test_ensure_label_exists_already(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)
    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.labels_list.return_value = [{"name": "agent/processed"}]
        await ch._ensure_label()
    mock_gog.labels_list.assert_called_once_with("bot@example.com")
    mock_gog.labels_create.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_label_creates_missing(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)
    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.labels_list.return_value = [{"name": "INBOX"}]
        await ch._ensure_label()
    mock_gog.labels_create.assert_called_once_with("agent/processed", "bot@example.com")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_email_channel.py::test_constructor tests/test_email_channel.py::test_ensure_label_exists_already tests/test_email_channel.py::test_ensure_label_creates_missing -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement EmailChannel constructor and label bootstrap**

Create `src/channels/email.py`:

```python
"""Email channel — polls Gmail via gog CLI, processes inbound messages, sends replies."""

import asyncio
import logging
import os

from src.channels import gog
from src.channels.base import IncomingMessage, OutgoingMessage
from src.config import EmailChannelConfig

logger = logging.getLogger(__name__)


class EmailChannel:
    def __init__(self, in_queue: asyncio.Queue, config: EmailChannelConfig):
        self.in_queue = in_queue
        self.config = config
        self.account = config.account
        self.poll_interval = config.poll_interval_sec
        self.allowed_senders = config.allowed_senders
        self.processed_label = config.processed_label
        self.attachment_dir = config.attachment_dir
        self.last_seen: dict[str, str] = {}

    async def _ensure_label(self) -> None:
        """Ensure the processed label exists in Gmail, creating it if missing."""
        labels = await asyncio.to_thread(gog.labels_list, self.account)
        if not any(label.get("name") == self.processed_label for label in labels):
            await asyncio.to_thread(gog.labels_create, self.processed_label, self.account)

    async def start(self) -> None:
        """Verify gog, bootstrap label, enter polling loop."""
        await asyncio.to_thread(gog.check_installed)
        await self._ensure_label()
        await self._poll_loop()

    async def send(self, msg: OutgoingMessage) -> None:
        """Send a reply and label the thread as processed."""
        pass  # implemented in Task 6

    async def _poll_loop(self) -> None:
        """Poll for new messages on an interval."""
        pass  # implemented in Task 5
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_email_channel.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/channels/email.py tests/test_email_channel.py
git commit -m "feat: add EmailChannel with constructor and label bootstrap"
```

---

### Task 5: Implement polling loop and message construction

**Files:**
- Modify: `src/channels/email.py`
- Modify: `tests/test_email_channel.py`

- [ ] **Step 1: Write failing test for `_poll_once()` — basic message**

Add to `tests/test_email_channel.py`:

```python
from src.channels.base import IncomingMessage


@pytest.mark.asyncio
async def test_poll_once_pushes_message(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)

    thread = {
        "id": "thread_1",
        "messages": [
            {
                "id": "msg_1",
                "from": "alice@example.com",
                "subject": "Hello",
                "body": "Hi there!",
                "attachments": [],
            }
        ],
    }

    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.search.return_value = [{"id": "thread_1"}]
        mock_gog.thread_get.return_value = thread
        await ch._poll_once()

    msg = in_queue.get_nowait()
    assert msg.content == "Hi there!"
    assert msg.channel == "email"
    assert msg.session_id == "thread_1"
    assert msg.reply_address == {
        "to": "alice@example.com",
        "subject": "Re: Hello",
        "in_reply_to": "msg_1",
    }
    assert msg.attachments is None
    assert ch.last_seen["thread_1"] == "msg_1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_email_channel.py::test_poll_once_pushes_message -v`
Expected: FAIL with `AttributeError: 'EmailChannel' object has no attribute '_poll_once'`

- [ ] **Step 3: Write failing test for sender filtering**

Add to `tests/test_email_channel.py`:

```python
@pytest.mark.asyncio
async def test_poll_once_filters_disallowed_sender(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)  # allowed_senders = ["alice@example.com"]

    thread = {
        "id": "thread_1",
        "messages": [
            {
                "id": "msg_1",
                "from": "stranger@example.com",
                "subject": "Spam",
                "body": "Buy stuff!",
                "attachments": [],
            }
        ],
    }

    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.search.return_value = [{"id": "thread_1"}]
        mock_gog.thread_get.return_value = thread
        await ch._poll_once()

    assert in_queue.empty()
    # last_seen still updated to avoid reprocessing
    assert ch.last_seen["thread_1"] == "msg_1"
```

- [ ] **Step 4: Write failing test for `last_seen` deduplication**

Add to `tests/test_email_channel.py`:

```python
@pytest.mark.asyncio
async def test_poll_once_skips_already_seen_messages(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)
    ch.last_seen["thread_1"] = "msg_1"  # already seen msg_1

    thread = {
        "id": "thread_1",
        "messages": [
            {
                "id": "msg_1",
                "from": "alice@example.com",
                "subject": "Hello",
                "body": "Hi there!",
                "attachments": [],
            },
            {
                "id": "msg_2",
                "from": "alice@example.com",
                "subject": "Re: Hello",
                "body": "Follow up!",
                "attachments": [],
            },
        ],
    }

    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.search.return_value = [{"id": "thread_1"}]
        mock_gog.thread_get.return_value = thread
        await ch._poll_once()

    msg = in_queue.get_nowait()
    assert msg.content == "Follow up!"
    assert msg.reply_address["in_reply_to"] == "msg_2"
    assert in_queue.empty()  # only one message pushed
    assert ch.last_seen["thread_1"] == "msg_2"
```

- [ ] **Step 5: Write failing test for no allowed_senders (accept all)**

Add to `tests/test_email_channel.py`:

```python
@pytest.mark.asyncio
async def test_poll_once_accepts_all_when_no_allowlist(in_queue):
    config = EmailChannelConfig(enabled=True, account="bot@example.com", poll_interval_sec=1)
    ch = EmailChannel(in_queue, config)

    thread = {
        "id": "thread_1",
        "messages": [
            {
                "id": "msg_1",
                "from": "anyone@example.com",
                "subject": "Hello",
                "body": "Hi!",
                "attachments": [],
            }
        ],
    }

    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.search.return_value = [{"id": "thread_1"}]
        mock_gog.thread_get.return_value = thread
        await ch._poll_once()

    assert not in_queue.empty()
```

- [ ] **Step 6: Write failing test for subject prefix handling**

Add to `tests/test_email_channel.py`:

```python
@pytest.mark.asyncio
async def test_poll_once_no_double_re_prefix(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)

    thread = {
        "id": "thread_1",
        "messages": [
            {
                "id": "msg_1",
                "from": "alice@example.com",
                "subject": "Re: Hello",
                "body": "Reply!",
                "attachments": [],
            }
        ],
    }

    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.search.return_value = [{"id": "thread_1"}]
        mock_gog.thread_get.return_value = thread
        await ch._poll_once()

    msg = in_queue.get_nowait()
    assert msg.reply_address["subject"] == "Re: Hello"  # not "Re: Re: Hello"
```

- [ ] **Step 7: Implement `_poll_once()` and `_poll_loop()`**

Replace the stub methods in `src/channels/email.py`:

```python
    async def _poll_loop(self) -> None:
        """Poll for new messages on an interval."""
        while True:
            try:
                await self._poll_once()
            except Exception:
                logger.exception("Error during email poll")
            await asyncio.sleep(self.poll_interval)

    async def _poll_once(self) -> None:
        """Run one poll cycle: search for unprocessed threads and process new messages."""
        query = f"-label:{self.processed_label}"
        threads = await asyncio.to_thread(gog.search, query, self.account)

        for thread_summary in threads:
            thread_id = thread_summary["id"]
            try:
                thread = await asyncio.to_thread(gog.thread_get, thread_id, self.account)
            except gog.GogError:
                logger.exception("Failed to fetch thread %s", thread_id)
                continue

            messages = thread.get("messages", [])
            last_seen_id = self.last_seen.get(thread_id)

            # Find new messages: skip up to and including last_seen
            new_messages = self._new_messages(messages, last_seen_id)

            for message in new_messages:
                self.last_seen[thread_id] = message["id"]

                sender = message.get("from", "")
                if self.allowed_senders and sender not in self.allowed_senders:
                    continue

                subject = message.get("subject", "")
                reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"

                attachments = await self._process_attachments(thread_id, message)

                content = message.get("body", "")
                if attachments:
                    content += "\n\nAttachments:\n"
                    for att in attachments:
                        size_kb = att["size"] // 1024
                        content += f"- {att['filename']} ({att['mime_type']}, {size_kb}KB) -> {att['path']}\n"

                incoming = IncomingMessage(
                    content=content,
                    channel="email",
                    session_id=thread_id,
                    reply_address={
                        "to": sender,
                        "subject": reply_subject,
                        "in_reply_to": message["id"],
                    },
                    attachments=attachments if attachments else None,
                )
                await self.in_queue.put(incoming)

    @staticmethod
    def _new_messages(messages: list[dict], last_seen_id: str | None) -> list[dict]:
        """Return messages after last_seen_id, or all if not seen before."""
        if last_seen_id is None:
            return messages

        found = False
        new = []
        for msg in messages:
            if found:
                new.append(msg)
            elif msg["id"] == last_seen_id:
                found = True
        return new

    async def _process_attachments(self, thread_id: str, message: dict) -> list[dict] | None:
        """Download and build attachment manifest if message has attachments."""
        raw_attachments = message.get("attachments", [])
        if not raw_attachments:
            return None

        out_dir = os.path.join(self.attachment_dir, thread_id)

        try:
            await asyncio.to_thread(gog.thread_download_attachments, thread_id, out_dir, self.account)
        except gog.GogError:
            logger.exception("Failed to download attachments for thread %s", thread_id)
            return None

        manifest = []
        for att in raw_attachments:
            manifest.append({
                "filename": att["filename"],
                "path": os.path.join(out_dir, att["filename"]),
                "mime_type": att.get("mimeType", "application/octet-stream"),
                "size": att.get("size", 0),
            })
        return manifest
```

- [ ] **Step 8: Run all poll tests**

Run: `python -m pytest tests/test_email_channel.py -v`
Expected: ALL PASS

- [ ] **Step 9: Write failing test for attachments**

Add to `tests/test_email_channel.py`:

```python
@pytest.mark.asyncio
async def test_poll_once_with_attachments(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)

    thread = {
        "id": "thread_1",
        "messages": [
            {
                "id": "msg_1",
                "from": "alice@example.com",
                "subject": "Report",
                "body": "See attached.",
                "attachments": [
                    {"filename": "report.pdf", "mimeType": "application/pdf", "size": 12288},
                ],
            }
        ],
    }

    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.search.return_value = [{"id": "thread_1"}]
        mock_gog.thread_get.return_value = thread
        mock_gog.thread_download_attachments.return_value = None
        await ch._poll_once()

    msg = in_queue.get_nowait()
    assert msg.attachments is not None
    assert len(msg.attachments) == 1
    assert msg.attachments[0]["filename"] == "report.pdf"
    assert msg.attachments[0]["path"] == "/tmp/attachments/thread_1/report.pdf"
    assert msg.attachments[0]["mime_type"] == "application/pdf"
    assert msg.attachments[0]["size"] == 12288
    assert "report.pdf" in msg.content
    assert "12KB" in msg.content
```

- [ ] **Step 10: Run test to verify it passes**

Run: `python -m pytest tests/test_email_channel.py::test_poll_once_with_attachments -v`
Expected: PASS (already implemented in step 7)

- [ ] **Step 11: Write test for poll error resilience** (verification test — implementation already handles this)

Add to `tests/test_email_channel.py`:

```python
@pytest.mark.asyncio
async def test_poll_once_continues_on_thread_error(email_config, in_queue):
    """If one thread fails to fetch, other threads still get processed."""
    ch = EmailChannel(in_queue, email_config)

    good_thread = {
        "id": "thread_2",
        "messages": [
            {"id": "msg_2", "from": "alice@example.com", "subject": "OK", "body": "Works!", "attachments": []},
        ],
    }

    from src.channels.gog import GogError

    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.GogError = GogError
        mock_gog.search.return_value = [{"id": "thread_1"}, {"id": "thread_2"}]
        mock_gog.thread_get.side_effect = [GogError("network error"), good_thread]
        await ch._poll_once()

    msg = in_queue.get_nowait()
    assert msg.content == "Works!"
```

- [ ] **Step 12: Run test to verify it passes**

Run: `python -m pytest tests/test_email_channel.py::test_poll_once_continues_on_thread_error -v`
Expected: PASS

- [ ] **Step 13: Commit**

```bash
git add src/channels/email.py tests/test_email_channel.py
git commit -m "feat: add email polling loop with message construction and deduplication"
```

---

### Task 6: Implement `send()` — outbound reply + label

**Files:**
- Modify: `src/channels/email.py`
- Modify: `tests/test_email_channel.py`

- [ ] **Step 1: Write failing test for send**

Add to `tests/test_email_channel.py`:

```python
from src.channels.base import OutgoingMessage


@pytest.mark.asyncio
async def test_send_reply_and_label(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)

    msg = OutgoingMessage(
        content="Got it, thanks!",
        channel="email",
        session_id="thread_1",
        reply_address={
            "to": "alice@example.com",
            "subject": "Re: Hello",
            "in_reply_to": "msg_1",
        },
    )

    with patch("src.channels.email.gog") as mock_gog:
        await ch.send(msg)

    mock_gog.send_reply.assert_called_once_with(
        to="alice@example.com",
        subject="Re: Hello",
        body="Got it, thanks!",
        reply_to_message_id="msg_1",
        account="bot@example.com",
    )
    mock_gog.thread_modify.assert_called_once_with(
        "thread_1", add_label="agent/processed", account="bot@example.com",
    )
```

- [ ] **Step 2: Write failing test for send failure (no label applied)**

Add to `tests/test_email_channel.py`:

```python
@pytest.mark.asyncio
async def test_send_failure_does_not_label(email_config, in_queue):
    ch = EmailChannel(in_queue, email_config)

    msg = OutgoingMessage(
        content="Reply text",
        channel="email",
        session_id="thread_1",
        reply_address={
            "to": "alice@example.com",
            "subject": "Re: Hello",
            "in_reply_to": "msg_1",
        },
    )

    from src.channels.gog import GogError

    with patch("src.channels.email.gog") as mock_gog:
        mock_gog.GogError = GogError
        mock_gog.send_reply.side_effect = GogError("send failed")
        await ch.send(msg)  # should not raise

    mock_gog.thread_modify.assert_not_called()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_email_channel.py::test_send_reply_and_label tests/test_email_channel.py::test_send_failure_does_not_label -v`
Expected: FAIL (send is a stub returning None, so mock assertions fail)

- [ ] **Step 4: Implement `send()`**

Replace the stub in `src/channels/email.py`:

```python
    async def send(self, msg: OutgoingMessage) -> None:
        """Send a reply in the original thread and label it as processed."""
        try:
            await asyncio.to_thread(
                gog.send_reply,
                to=msg.reply_address["to"],
                subject=msg.reply_address["subject"],
                body=msg.content,
                reply_to_message_id=msg.reply_address["in_reply_to"],
                account=self.account,
            )
        except gog.GogError:
            logger.exception("Failed to send reply for thread %s", msg.session_id)
            return

        try:
            await asyncio.to_thread(
                gog.thread_modify,
                msg.session_id,
                add_label=self.processed_label,
                account=self.account,
            )
        except gog.GogError:
            logger.exception("Failed to label thread %s as processed", msg.session_id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_email_channel.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/channels/email.py tests/test_email_channel.py
git commit -m "feat: implement email channel send with reply and label"
```

---

## Chunk 4: Wiring and Integration

### Task 7: Wire up EmailChannel in `run.py`

**Files:**
- Modify: `run.py`

- [ ] **Step 1: Verify existing tests pass before modifying run.py**

Run: `python -m pytest -v`
Expected: ALL PASS

- [ ] **Step 2: Add email channel wiring to `run.py`**

Make these specific changes to `run.py`:

**Add import** — insert after the existing `import json` line:

```python
import os
```

**Add import** — insert after the existing `from src.channels.cli import CLIChannel` line:

```python
from src.channels.email import EmailChannel
```

**Modify import** — change the existing config import:

```python
# Before:
from src.config import AgentConfig
# After:
from src.config import AgentConfig, EmailChannelConfig
```

**Add email channel setup** — insert after `channels = {"cli": cli}` and before the `# Start all channels` comment:

```python
    # Email channel (conditional)
    email_config = EmailChannelConfig(
        enabled=os.environ.get("EMAIL_ENABLED", "false").lower() == "true",
        account=os.environ.get("GOG_ACCOUNT", ""),
        poll_interval_sec=int(os.environ.get("EMAIL_POLL_INTERVAL", "60")),
        allowed_senders=[s.strip() for s in os.environ.get("EMAIL_ALLOWED_SENDERS", "").split(",") if s.strip()],
        processed_label=os.environ.get("EMAIL_PROCESSED_LABEL", "agent/processed"),
        attachment_dir=os.environ.get("EMAIL_ATTACHMENT_DIR", "/tmp/attachments"),
    )
    if email_config.enabled:
        email_channel = EmailChannel(in_queue, email_config)
        channels["email"] = email_channel
```

The existing `async with asyncio.TaskGroup()` block is unchanged — it already iterates `channels.values()`, so the email channel will be started automatically.

- [ ] **Step 3: Run all tests to verify nothing broke**

Run: `python -m pytest -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add run.py
git commit -m "feat: wire up email channel in run.py when EMAIL_ENABLED=true"
```

---

### Task 8: Final integration check

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest -v`
Expected: ALL PASS

- [ ] **Step 2: Verify import chain works**

Run: `python -c "from src.channels.email import EmailChannel; from src.channels.gog import check_installed; from src.config import EmailChannelConfig; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 3: Verify linting**

Run: `python -m ruff check src/channels/email.py src/channels/gog.py src/config.py run.py`

- [ ] **Step 4: Final commit if any fixes were needed**

```bash
git add src/channels/email.py src/channels/gog.py src/config.py run.py
git commit -m "fix: address linting issues in email channel"
```
