# Email Channel Design

## Overview

The email channel enables the agent to converse with users over email. It polls Gmail for new messages using the `gog` CLI tool, processes them through the agent loop, and sends replies within the original email thread.

Sessions are initiated by inbound email only — the agent never sends cold outbound messages. A Gmail label tracks which threads have been processed.

## Approach

Shell out to `gog` (gogcli) for all Gmail operations: search, thread retrieval, sending, label management, and attachment download. All commands use `--json` for machine-parseable output. No Gmail API code — `gog` handles auth, pagination, MIME parsing, and threading.

## Configuration

```python
@dataclass
class EmailChannelConfig:
    enabled: bool = False
    account: str = ""                    # gog account email (from GOG_ACCOUNT)
    poll_interval_sec: int = 60
    allowed_senders: list[str] = field(default_factory=list)  # empty = accept all
    processed_label: str = "agent/processed"
    attachment_dir: str = "/tmp/attachments"
```

Environment variables (via `.env`):

| Variable | Purpose |
|----------|---------|
| `GOG_ACCOUNT` | Gmail account for gog |
| `GOG_KEYRING_PASSWORD` | Encrypted keyring password for gog auth |
| `GOG_KEYRING_BACKEND` | Set to `file` for Docker |
| `EMAIL_ENABLED` | Enable the email channel |
| `EMAIL_POLL_INTERVAL` | Poll interval in seconds (default 60) |
| `EMAIL_ALLOWED_SENDERS` | Comma-separated sender allowlist |
| `EMAIL_PROCESSED_LABEL` | Gmail label for processed threads |
| `EMAIL_ATTACHMENT_DIR` | Directory for downloaded attachments |

Until the `config.yaml` loader is implemented, `run.py` hydrates `EmailChannelConfig` from environment variables directly.

## Channel Structure

`src/channels/email.py` implements the `Channel` protocol:

```python
class EmailChannel:
    async def start(self)                   # Label bootstrap + polling loop
    async def send(self, msg: OutgoingMessage)  # Reply + label as processed
```

### Startup

1. Verify `gog` is installed (fail fast if not)
2. Ensure the `processed_label` exists in Gmail (create if missing via `gog gmail labels create`)
3. Enter polling loop

## Polling Loop

Each poll cycle:

1. **Search** for unprocessed emails:
   ```
   gog gmail search 'label:inbox -label:<processed_label>' --json --max 20 --account <account>
   ```

2. **Filter** results by `allowed_senders` (if configured)

3. **For each matching thread:**
   a. Fetch thread: `gog gmail thread get <threadId> --json --account <account>`
   b. Find the newest unprocessed message (skip messages with ID <= `last_seen[thread_id]`)
   c. If the sender is not in `allowed_senders` (when configured), skip
   d. If the message has attachments:
      ```
      gog gmail thread get <threadId> --download --out-dir <attachment_dir>/<threadId>/
      ```
   e. Build `IncomingMessage`:
      - `content`: plain text body (strip HTML tags if only HTML available)
      - `channel`: `"email"`
      - `session_id`: `threadId`
      - `reply_address`: `{to: <sender>, subject: "Re: <subject>", reply_to_message_id: <messageId>}`
      - `attachments`: `[{filename, path, mime_type, size}, ...]` (manifest)
   f. Push to `in_queue`
   g. Update `last_seen[thread_id] = messageId`

4. **Sleep** `poll_interval_sec`, repeat

### Deduplication

Two layers:

- **Durable (survives restart):** The `processed_label` on the Gmail thread. Threads with this label are excluded from the search query.
- **In-memory (within a run):** `last_seen: dict[str, str]` mapping `thread_id → last_processed_message_id`. Prevents reprocessing old messages when a new message arrives in an already-seen thread before it gets labeled.

### Message Ordering

Threads are processed oldest-unprocessed-first so conversations flow naturally through the queue.

## Outbound Reply

When the router calls `email_channel.send(msg)`:

1. **Send reply** in the original thread:
   ```
   gog gmail send \
     --reply-to-message-id <reply_address.reply_to_message_id> \
     --to <reply_address.to> \
     --subject <reply_address.subject> \
     --body <content> \
     --account <account>
   ```

2. **Label thread as processed:**
   ```
   gog gmail thread modify <session_id> --add <processed_label> --account <account>
   ```

If the send fails, the processed label is not applied — the thread will be picked up again on the next poll.

## Attachments

### IncomingMessage Change

Add an optional `attachments` field to `IncomingMessage` in `src/channels/base.py`:

```python
@dataclass
class IncomingMessage:
    content: str
    channel: str
    session_id: str
    reply_address: dict
    command: str | None = None
    attachments: list[dict] | None = None  # [{filename, path, mime_type, size}]
```

CLI and Slack channels pass `None` (default). No impact on existing code.

### Attachment Flow

1. During poll, `gog gmail thread get <threadId> --download --out-dir <attachment_dir>/<threadId>/` saves attachments to disk
2. Build manifest from downloaded files: `[{filename, path, mime_type, size}]`
3. Set `IncomingMessage.attachments` to the manifest
4. Append attachment summary to message content so the agent knows what's available:
   ```
   [Original email body]

   Attachments:
   - report.pdf (application/pdf, 12KB) -> /tmp/attachments/<threadId>/report.pdf
   - image.png (image/png, 45KB) -> /tmp/attachments/<threadId>/image.png
   ```
5. Agent uses `Read` or `Bash` tools to inspect attachments as needed

### Cleanup

No active cleanup in v1. The container is ephemeral — attachments are cleaned up when the container stops.

## Label Bootstrap

On startup, before polling begins:

1. List existing labels: `gog gmail labels list --json --account <account>`
2. If `processed_label` is not present: `gog gmail labels create <processed_label> --account <account>`

Runs once, idempotent.

## Error Handling

- **`gog` not installed** — fail fast at startup with clear error message
- **`gog` command fails** (non-zero exit) — log error, skip that thread/message, continue polling
- **Malformed JSON from `gog`** — log error, skip, continue
- **Send failure** — log error, do not apply processed label (retried next poll)
- **No retry logic, no backoff** — if a poll fails entirely, wait for next interval

## Docker / Auth

The container needs:

- `gog` binary installed in the Docker image
- File-based keyring (set `GOG_KEYRING_BACKEND=file`)
- `GOG_KEYRING_PASSWORD` in `.env`
- Pre-authenticated keyring file copied into the image (or volume-mounted)

All secrets provided via `.env` file.

## Files

| File | Action |
|------|--------|
| `src/channels/base.py` | Add `attachments` field to `IncomingMessage` |
| `src/channels/email.py` | Create — `EmailChannel` with poll loop + send |
| `src/config.py` | Add `EmailChannelConfig` dataclass |
| `run.py` | Wire up `EmailChannel` when enabled |
| `tests/test_email_channel.py` | Create — tests for email channel |

## What This Design Excludes

- Gmail push notifications / webhooks (polling only)
- Outbound-initiated emails (sessions start from inbound only)
- HTML email composition (plain text replies only)
- Attachment sending (inbound attachments only)
- Retry/backoff logic
- Persistent attachment storage
