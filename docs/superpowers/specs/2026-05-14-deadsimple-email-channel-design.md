# Deadsimple Email Channel Design

## Overview

Replace the Gmail-backed email transport with [deadsimple.email](https://deadsimple.email)'s HTTP API. The channel's public role does not change: inbound mail becomes `IncomingMessage(channel="email", session_id=<email-thread-id>, ...)`, replies are sent back into the same thread. What changes is everything *behind* `EmailChannel`: the API client, the polling shape, the "processed" tracking, and the configuration.

Gmail is removed entirely. There is no transition period and no provider-abstraction layer.

## Approach

Rewrite `src/channels/email.py` in place. The class stays `EmailChannel`, the channel name stays `"email"`, the `Channel` protocol (`start()` + `send()`) is unchanged. Internally it talks to deadsimple via a new `src/channels/deadsimple.py` HTTP client that replaces `src/channels/gmail.py` one-for-one in role.

Why in place rather than a new `DeadsimpleChannel` class: the channel's identity is email, not the provider. Renaming would churn every `channel == "email"` check, the router, the docs, and the tests for no gain — Gmail is being deleted.

## Deadsimple API: relevant facts

From `https://deadsimple.email/openapi.yaml` (v0.1.0). Only the parts we use are listed.

**Auth.** `Authorization: Bearer dse_<api_key>` on every request.

**Inbox model.** An account holds N inboxes. Each inbox is `{inbox_id (UUID), email, local_part, domain, ...}` and all message endpoints are scoped under `/v1/inboxes/{inbox_id}`. The channel uses exactly one inbox per agent, identified by `DEADSIMPLE_INBOX_ID`. Inbox creation is out of scope — the operator creates the inbox via dashboard or `POST /v1/inboxes` once and pastes the ID into `.env`.

**Messages.**

| Endpoint | Use |
|---|---|
| `GET /v1/inboxes/{inbox_id}/messages?limit&cursor` | List messages (both directions; client-side filter) |
| `GET /v1/inboxes/{inbox_id}/messages/{message_id}` | Full message body, headers, attachments metadata |
| `POST /v1/inboxes/{inbox_id}/messages` | Send a new email (also used for replies that carry attachments — see below) |
| `POST /v1/inboxes/{inbox_id}/messages/{message_id}/reply` | Reply with auto-set threading headers (no attachments) |
| `GET /v1/inboxes/{inbox_id}/messages/{message_id}/attachments/{attachment_id}` | Returns a signed download URL (1h TTL) |

`Message` carries a server-assigned `thread_id` (UUID) — deadsimple does RFC 2822 threading server-side. Other fields we use: `direction` (`inbound`/`outbound`), `from_email`, `to[]`, `cc[]`, `subject`, `text_body`, `html_body`, `attachments[]`, `created_at`, `is_spam`, `spam_score`.

**Idempotency.** `Idempotency-Key` header on POSTs returns the cached response for 24h. Used on every send to make worker retries safe.

**Rate limits.** Headers `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`. On 429, sleep until `X-RateLimit-Reset` (or fall back to exponential backoff if the header is missing) and retry the request once.

**Webhooks.** Available but unused — this channel polls. (See "What this design excludes.")

## Configuration

```python
@dataclass
class EmailChannelConfig:
    enabled: bool = False
    api_key: str = ""
    inbox_id: str = ""
    api_base: str = "https://api.deadsimple.email"
    poll_interval_sec: int = 60
    allowed_senders: list[str] = field(default_factory=list)
    attachment_dir: str = "/tmp/attachments"
    state_file: Path = Path("./context/email_state.json")
    spam_score_threshold: float = 5.0
    restrict_outbound: bool = True
```

Environment variables:

| Variable | Purpose |
|---|---|
| `EMAIL_ENABLED` | Enable the channel (default `false`) |
| `DEADSIMPLE_API_KEY` | API key, format `dse_...` |
| `DEADSIMPLE_INBOX_ID` | UUID of the inbox to poll and send from |
| `DEADSIMPLE_API_BASE` | Override the API base URL (optional, for testing) |
| `EMAIL_POLL_INTERVAL` | Poll interval in seconds (default 60) |
| `EMAIL_ALLOWED_SENDERS` | Comma-separated inbound + outbound allowlist (empty = no filter) |
| `EMAIL_RESTRICT_OUTBOUND` | If `true` (default), block outbound to addresses outside the allowlist |
| `EMAIL_ATTACHMENT_DIR` | Directory for downloaded attachments (default `/tmp/attachments`) |
| `EMAIL_STATE_FILE` | Path to persisted watermark (default `./context/email_state.json`) |
| `EMAIL_SPAM_SCORE_THRESHOLD` | Drop inbound where `spam_score >= this` (default 5.0) |

Removed: `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_DELEGATED_USER`, `EMAIL_PROCESSED_LABEL`.

`run.py` builds `EmailChannelConfig` from env exactly as it does today (one section replaces the other).

## Channel Structure

`src/channels/email.py`:

```python
class EmailChannel:
    def __init__(self, in_queue: asyncio.Queue, config: EmailChannelConfig):
        self.in_queue = in_queue
        self.config = config
        self.client = DeadsimpleClient(config.api_key, config.api_base, config.inbox_id)
        self.state = EmailState.load(config.state_file)   # {"watermark": "<iso8601>"}

    async def start(self) -> None
    async def send(self, msg: OutgoingMessage) -> None
```

`EmailState` is a thin wrapper around `state_file` with atomic write (`os.replace`). The file holds two fields:

```json
{"watermark_created_at": "2026-05-14T15:30:00Z", "watermark_message_id": "<uuid>"}
```

Both are needed for tie-breaking on identical `created_at` timestamps (see "Watermark edge cases" below). No SQLite.

### Startup

1. Validate the inbox: `GET /v1/inboxes/{inbox_id}` — fail fast on 401/404 with a clear error.
2. Log the inbox's email address.
3. If `state_file` is missing or empty (first ever run on a fresh container with no persisted state): set `watermark_created_at = utcnow()`, `watermark_message_id = ""`, write the file. This skips historical mail, matching today's behavior of processing only the latest message on first encounter. On a restart with an existing state file, the watermark is loaded and polling resumes from where it left off — no historical re-processing, no gap.
4. Enter polling loop.

## Polling Loop

Each cycle:

1. **Fetch new inbound messages** via cursor pagination on `GET /v1/inboxes/{inbox_id}/messages?limit=50`. The list is server-sorted newest-first. Walk pages until we see a message whose `(created_at, message_id) <= (watermark_created_at, watermark_message_id)` lexicographically. Stop there. From the walked set, keep only `direction == "inbound"`. Reverse to chronological order.

2. **For each new message:**
   a. Drop if `is_spam` or `spam_score >= spam_score_threshold` (log at INFO).
   b. Drop if `from_email` is not allow-listed (when `allowed_senders` is non-empty). Substring match, as today.
   c. Fetch the full message via `GET /v1/inboxes/{inbox_id}/messages/{message_id}`. The list endpoint's per-message payload is not documented to include `text_body`/`html_body`/`attachments`, so the channel always fetches detail. One extra request per new message, cheap given polling cadence and inbox volume.
   d. Download attachments (see below).
   e. Build the `IncomingMessage`:
      - `content`: `text_body`, falling back to text-extracted `html_body` (reuse the simple strip we have today; nothing fancy).
      - `channel`: `"email"`.
      - `session_id`: `thread_id`.
      - `reply_address`: `{"to": from_email, "subject": "Re: <subject>" if not already prefixed, "in_reply_to": message_id}`.
      - `attachments`: manifest from the download step, or `None`.
   f. `await self.in_queue.put(incoming)`.

3. **Update watermark.** After all messages in the batch are queued, set `(watermark_created_at, watermark_message_id)` to the lexicographically-greatest `(created_at, message_id)` pair among processed messages and atomically write the state file. If the batch is empty, watermark is unchanged.

4. **Sleep `poll_interval_sec`, repeat.**

### Deduplication

One layer: durable watermark (`created_at` timestamp) in `email_state.json`. Survives restarts. No in-memory `last_seen` map.

Why drop the in-memory dedup that Gmail had: Gmail needed it because labels are applied *after* send, leaving a race window during which a new arrival in an already-touched thread would get re-listed. With deadsimple, the list-by-cursor + watermark-by-created_at pattern has no such race — once a message's `created_at` is `<= watermark`, it cannot reappear.

### Watermark edge cases

- **Ties on `created_at`.** Two messages with identical `created_at` and one is the watermark: the `<=` filter would re-skip the kept one, and a sibling at the exact same instant would be incorrectly skipped. Mitigation: persist `(watermark_created_at, watermark_message_id)` and exclude only when `(created_at, message_id) <= (watermark_created_at, watermark_message_id)` lexicographically. UUID tie-break is good enough.
- **Clock skew.** `created_at` is server-assigned by deadsimple; we never compare it to local clock. Safe.

## Outbound (send)

When the router calls `email_channel.send(msg)`:

1. Skip if `not msg.final or not msg.content` (today's streaming-delta guard, kept).
2. Outbound allowlist check (see below).
3. Choose endpoint:
   - **No attachments:** `POST /v1/inboxes/{inbox_id}/messages/{in_reply_to}/reply` with `{"text_body": msg.content}`. Server auto-sets threading headers.
   - **With attachments:** `POST /v1/inboxes/{inbox_id}/messages` with:
     ```json
     {
       "to": [reply_address.to],
       "subject": reply_address.subject,
       "text_body": msg.content,
       "in_reply_to": reply_address.in_reply_to,
       "references": [reply_address.in_reply_to],
       "attachments": [{"filename": ..., "content_type": ..., "data": "<base64>"}, ...]
     }
     ```
     Server still threads correctly because we pass the headers explicitly.
4. Headers: `Authorization: Bearer ...`, `Idempotency-Key: <in_reply_to>-reply`.
5. On 429, sleep per `X-RateLimit-Reset` and retry once. On other 5xx, log and return — message stays in the conversation, next user action will surface the gap.

### Outbound allowlist

Today's behavior (see `gmail.py:_check_recipients_allowed`) is kept and moved to `deadsimple.py`:

```python
def _check_recipients_allowed(allowed: list[str], *recipients: str | None) -> None:
    if not config.restrict_outbound or not allowed:
        return
    blocked = [r for r in flatten(recipients) if not any(a in r for a in allowed)]
    if blocked:
        raise DeadsimpleError(f"Outbound blocked: {blocked} not in EMAIL_ALLOWED_SENDERS")
```

Called from the channel before the API call, against `to + cc + bcc`. Same semantics as today.

## Attachments

### Inbound

`Message.attachments` (list endpoint or detail endpoint) gives:

```json
[{"attachment_id": "<uuid>", "filename": "...", "content_type": "...", "size": 12345}]
```

For each entry:

1. `GET /v1/inboxes/{inbox_id}/messages/{message_id}/attachments/{attachment_id}` → response includes a signed URL.
2. `GET <signed_url>` → bytes.
3. Save to `<attachment_dir>/<thread_id>/<filename>`.
4. Reuse the existing helpers: `_normalize_unicode_whitespace`, `_validate_attachment_metadata`, `_normalize_filenames` (these live in `src/channels/_attachments.py`, already provider-agnostic).
5. Build the manifest entry: `{"filename", "path", "mime_type", "size"}`.

Failure on any single attachment: log and skip that attachment; don't drop the message.

### Outbound

Multipart upload is not supported by the API — attachments are inline base64 in the JSON body. For each path in `msg.attachments`:

```python
with open(path, "rb") as f:
    data = base64.b64encode(f.read()).decode("ascii")
attachments.append({"filename": basename(path), "content_type": mime, "data": data})
```

This forces the "with attachments" send path (POST messages, not POST reply).

### Cleanup

Unchanged: ephemeral container, no active cleanup. `EMAIL_ATTACHMENT_DIR` is wiped when the container stops.

## Error Handling

- **Invalid inbox at startup** (401/403/404) — fail fast with a clear error message naming the env var.
- **API errors during poll** — log, sleep, retry next cycle. Watermark is only advanced on a successful batch.
- **429 during poll** — honor `X-RateLimit-Reset` (computed from header), then retry once; if it still fails, give up this cycle.
- **Send 429** — same: one retry honoring the header, then drop and log.
- **Send 4xx other** — log and return (do not retry; idempotency key would just re-fail).
- **Attachment download fails** — drop that attachment, keep the message.
- **State file corrupt or missing** — treat as first run (watermark = now).

## Files

| File | Action |
|---|---|
| `src/channels/email.py` | Rewrite — `EmailChannel` on deadsimple, public surface unchanged |
| `src/channels/deadsimple.py` | Create — HTTP client (httpx async), `DeadsimpleError`, allowlist enforcement |
| `src/channels/gmail.py` | Delete |
| `src/channels/_attachments.py` | Unchanged — kept as the provider-agnostic helpers |
| `src/config.py` | Replace `EmailChannelConfig` fields |
| `run.py` | Wire new env vars; remove `GOOGLE_*` references in the email block |
| `tests/test_channels.py` | Swap Gmail mocks for `respx` HTTP mocks; new tests for: poll-page-walk, watermark advance, spam filter, allowed-senders, reply (no attach), reply-with-attach send path, 429 retry, attachment download |
| `requirements.txt` | Add `httpx`, `respx` (test); remove `google-api-python-client`, `google-auth` |
| `.env.example` | Update to deadsimple variables; remove Google ones |
| `docs/gmail-setup.md` | Delete or replace with a `deadsimple-setup.md` (one paragraph: create inbox in dashboard, copy API key + inbox_id into `.env`) |
| `docs/architecture.md` | Update the Channels section and add an ADR for the provider switch |
| `README.md` | Update the email setup snippet and feature list |
| `Dockerfile` / `docker-compose.yml` | Remove any Google credential mounts; no new mounts needed |

## Tests

`tests/test_channels.py` adds an `EmailChannel` block using `respx` to mock the deadsimple endpoints. Each test asserts both the HTTP calls made and the `IncomingMessage` / `OutgoingMessage` shapes produced.

Coverage targets:

- **Poll, empty inbox** — no work, watermark unchanged.
- **Poll, single inbound** — `IncomingMessage` shape correct; watermark advances.
- **Poll, mix of inbound/outbound** — only inbound queued.
- **Poll, spam dropped** — `is_spam=true` and `spam_score >= threshold` both filtered.
- **Poll, allowlist filter** — sender outside list is dropped; inside list passes.
- **Poll, multi-page walk** — pagination terminates correctly when watermark is hit mid-page.
- **Poll, watermark tie-break** — two messages at same `created_at`, watermark distinguishes by `message_id`.
- **Send, no attachments** — hits `/reply` endpoint, body shape correct, `Idempotency-Key` set.
- **Send, with attachments** — hits `/messages` endpoint with `in_reply_to` + `references` + base64 attachments.
- **Send, blocked by outbound allowlist** — raises, no HTTP call made.
- **Send, 429 then 200** — retries once respecting `X-RateLimit-Reset`.
- **Attachment download** — signed-URL flow saves bytes, manifest entry correct.
- **Attachment download failure** — message still queued, that attachment absent from manifest.
- **State file** — created on first run with `watermark = now`; reload preserves value; corrupt file is treated as first run.

## Migration / Operator Steps

1. Create a deadsimple account (or use existing).
2. Create an API key with `read,write` scopes — note it.
3. Create an inbox in the dashboard (or `POST /v1/inboxes`). Note the `inbox_id`.
4. Update `.env`: set `DEADSIMPLE_API_KEY`, `DEADSIMPLE_INBOX_ID`. Remove `GOOGLE_*` variables.
5. Delete any prior Gmail service-account credential files mounted into the container.
6. Restart the container. First poll skips historical mail (watermark = now); subsequent polls deliver new mail.

No data migration. Prior Gmail threads are not imported — they live in Gmail.

## What This Design Excludes

- Webhook-based inbound delivery (deadsimple supports it; we chose polling for parity and simpler ops).
- Custom domains (`/v1/domains/*`). The operator can configure these out-of-band; the channel doesn't care.
- Open/click tracking (`tracking: true` on send, tracking endpoints). Not needed for agent replies.
- Templates (`template_id`, `variables` on send). Not needed.
- Multi-inbox support. One inbox per agent. If you want more, run another agent.
- Importing prior Gmail mail.
- HTML composition. Plain text replies only, same as today.
- Reply-all / forward endpoints. The agent uses one-to-one replies.
- A provider-abstraction layer. YAGNI — there's exactly one provider.
