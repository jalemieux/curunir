# Gmail OAuth Token Auto-Renewal

**Date:** 2026-03-23
**Issue:** https://github.com/jalemieux/curunir/issues/11
**Status:** Draft

## Problem

When the Gmail OAuth refresh token expires or is revoked, the email channel fails with `invalid_grant` and keeps retrying every poll cycle. The user has no way to know their token is dead until they notice emails aren't being processed.

Re-auth currently requires manual steps:
1. Run `gog auth login` on a machine with a browser
2. `gog auth tokens export` to `secrets/gog-token.json`
3. Restart the container

This is unacceptable for multi-user deployments and fragile for self-hosted setups.

## Design

### Detection & Notification

When the email channel catches an `invalid_grant` error from `gog`:

1. **Stop polling** — no point retrying until re-authed
2. **Generate a Google OAuth authorization URL** using the credentials from `secrets/gog-credentials.json`
3. **Send a notification** to the user via the websocket channel with the auth link
4. **Periodically re-send** the notification if the user hasn't re-authed (in case they weren't connected when it first fired)

The `invalid_grant` error is detected by matching the string in `GogError` exceptions. Other `GogError`s (network issues, rate limits) should NOT trigger the re-auth flow — only `invalid_grant` or `Token has been expired or revoked`.

### OAuth Callback (Method A — Primary)

Curunir adds HTTP routes for the OAuth flow. These can be served on the existing websocket port (8765) using aiohttp or similar, or on a separate port.

- `GET /auth/gmail` — redirects to Google's consent screen with appropriate scopes
- `GET /auth/gmail/callback` — receives the authorization code, exchanges it for tokens, imports into `gog`, restarts email polling

The callback URL must be registered in the Google Cloud Console as an authorized redirect URI.

### Paste-Back Fallback (Method B)

If the user can't reach curunir's callback URL from their browser (e.g. behind NAT without a domain):

- The notification includes a second link using Google's OOB/copy-paste flow
- User completes consent, gets a code, pastes it back via the websocket channel (e.g. as a special command like `/auth-gmail <code>`)
- Curunir exchanges the code for tokens and resumes

### Token Exchange

Curunir handles the OAuth token exchange directly using `google-auth` library or raw HTTP POST to `https://oauth2.googleapis.com/token`. No dependency on `gog auth login` (which requires a browser on the same machine).

After obtaining the refresh token:
1. Write it to the token file (`/secrets/gog-token.json` or equivalent path)
2. Run `gog auth tokens import` to load it into gog's keyring
3. Transition email channel back to `polling` state

### Email Channel State Machine

```
polling ──(invalid_grant)──> auth_required
auth_required ──(notification sent)──> waiting_for_auth
waiting_for_auth ──(token received)──> polling
```

- **polling**: Normal operation — poll Gmail on interval
- **auth_required**: `invalid_grant` detected, generate auth URL, send notification
- **waiting_for_auth**: Auth URL sent to user, waiting for OAuth callback or paste-back. Periodically re-send notification.
- **polling**: Token refreshed, resume normal operation

### Files Changed

- `src/channels/email.py` — Add state machine, detect `invalid_grant`, trigger notification
- `src/channels/gog.py` — Add `is_auth_error()` helper to classify errors
- `src/auth/gmail.py` (new) — OAuth URL generation, token exchange, callback handler
- `src/channels/ws.py` — Ensure system notifications can be sent to connected clients
- `entrypoint.sh` — No changes needed (token import at startup remains)

### Configuration

- `GOG_OAUTH_REDIRECT_URI` env var — The callback URL for Method A (e.g. `https://curunir.example.com/auth/gmail/callback`). If unset, only Method B (paste-back) is available.
- Credentials file (`secrets/gog-credentials.json`) is already mounted and contains the OAuth client ID/secret.

### Scope

- No new database or persistent state — just the token file
- Notification via websocket channel only (for now)
- Gmail/Google OAuth only (not a generic OAuth framework)

### Out of Scope

- Slack/webhook/other notification channels
- Proactive token refresh before expiry
- Multi-provider OAuth
- Web UI for auth management (the auth URL is the UI)
