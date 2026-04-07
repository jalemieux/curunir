# Gmail Service Account Migration Design

**Issue:** jalemieux/curunir#17
**Date:** 2026-04-06

## Summary

Replace the `gog` CLI-based Gmail integration with direct Google Workspace service account auth using `google-auth` and `google-api-python-client`. Eliminates OAuth token management, the external `gog` binary, and interactive auth flows.

## Approach

Drop-in replacement: new `gmail.py` module exposes the same function signatures as `gog.py`. The email channel (`email.py`) swaps its import with minimal other changes.

## Changes

### 1. New `src/channels/gmail.py`

Replaces `gog.py`. Provides the same public functions:

- `labels_list(service) -> list[dict]`
- `labels_create(label, service) -> None`
- `search(query, service, max_results=20) -> list[dict]`
- `thread_get(thread_id, service) -> dict`
- `send_reply(to, subject, body, reply_to_message_id, service, attachments=None) -> None`
- `thread_modify(thread_id, add_label, service) -> None`
- `download_attachments(thread_id, message, out_dir, service) -> None`

**Key difference:** functions accept a Gmail API `service` object instead of an `account` string. The service is built once during channel init and passed through.

**Service construction:**
```python
from google.oauth2 import service_account
from googleapiclient.discovery import build

credentials = service_account.Credentials.from_service_account_file(
    service_account_file,
    scopes=['https://www.googleapis.com/auth/gmail.modify'],
    subject=delegated_user,
)
service = build('gmail', 'v1', credentials=credentials)
```

**Message normalization:** The `_normalize_message`, `_get_header`, `_decode_body`, and `_extract_attachments` helpers move from `gog.py` to `gmail.py` unchanged — the raw Gmail API response format is the same (gog was just proxying it).

**Attachment downloads:** Instead of shelling out to `gog --download`, call `messages.attachments.get()` for each attachment and write the file to disk using the original filename (no ID prefix). This simplifies the matching logic in `email.py`.

**Error handling:** `GmailError` replaces `GogError` as the module-level exception.

### 2. `src/channels/email.py`

- Change import: `from src.channels import gog` -> `from src.channels import gmail`
- Build the Gmail API service object in `__init__` (or lazily on first use) from config
- Pass `self.service` to all `gmail.*` calls instead of `self.account`
- Replace `gog.GogError` -> `gmail.GmailError`
- Simplify `_process_attachments`: since we now write files with their original filename (no gog prefix), drop the suffix-matching fallback logic. Match downloaded files by exact name only.
- Remove `check_installed` call from `start()` (no CLI binary to verify)

### 3. `src/config.py`

Update `EmailChannelConfig`:
- Remove `account` field
- Add `service_account_file: str = ""` — path to the GCP service account JSON key
- Add `delegated_user: str = ""` — Workspace email to impersonate

### 4. `run.py`

- Read `GOOGLE_SERVICE_ACCOUNT_FILE` and `GOOGLE_DELEGATED_USER` instead of `GOG_ACCOUNT`
- Remove `GOG_ACCOUNT` reference

### 5. `requirements.txt`

Add:
- `google-auth`
- `google-api-python-client`

### 6. `Dockerfile`

Remove the gog CLI installation block:
```dockerfile
# Remove this:
ARG GOG_VERSION=0.12.0
RUN curl -fsSL "https://github.com/steipete/gogcli/releases/download/v${GOG_VERSION}/gogcli_${GOG_VERSION}_linux_amd64.tar.gz" \
    | tar -xz -C /usr/local/bin gog && \
    chmod +x /usr/local/bin/gog
```

### 7. `entrypoint.sh`

Remove the entire `--- gog CLI setup ---` section (lines 3-30). The service account JSON key will be mounted as a volume or baked into the image — no runtime token import needed.

### 8. `.env.example`

Replace:
```
# Email channel (Gmail via gog CLI)
# GOG_ACCOUNT=you@gmail.com
# GOG_KEYRING_PASSWORD=curunir
```

With:
```
# Email channel (Gmail via Google Workspace service account)
# GOOGLE_SERVICE_ACCOUNT_FILE=/secrets/service-account.json
# GOOGLE_DELEGATED_USER=you@yourdomain.com
```

### 9. Delete `src/channels/gog.py`

No longer needed.

### 10. Tests

**`tests/test_gmail.py`** (replaces `test_gog.py`):
- Mock `googleapiclient.discovery.build` and the service object's method chains
- Test each public function: labels_list, labels_create, search, thread_get, send_reply, thread_modify, download_attachments
- Test message normalization (body decoding, header extraction, nested attachments)
- Test error handling (API exceptions -> GmailError)

**`tests/test_email_channel.py`**:
- Change all `patch("src.channels.email.gog")` to `patch("src.channels.email.gmail")`
- Update mock setup to match new function signatures (service object instead of account string)
- Remove gog-prefix attachment test (`test_poll_once_with_prefixed_attachments`) — no longer relevant
- Attachment tests use exact filenames since we write files directly

## Out of Scope

- `skills/email-send/SKILL.md` rewrite (will be done separately)
- GCP setup documentation (manual prerequisite, documented in issue #17)

## Acceptance Criteria

- Email channel authenticates via service account JSON key
- No `gog` dependency anywhere in the codebase (except the deferred skill rewrite)
- Works headless in Docker without interactive auth
- Existing email functionality preserved (poll inbox, send replies, attachments)
- Tests updated and passing
- `.env.example` and Dockerfile updated
