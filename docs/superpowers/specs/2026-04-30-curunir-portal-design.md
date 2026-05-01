# Curunir Portal — Design

A hosted multi-user web portal that lets each user chat with their own
self-hosted curunir container from anywhere — phone or desktop —
without CLI or email roundtrips.

## Problem

Today, a user can talk to curunir three ways:

1. **CLI** (`cli.py`) — requires being on the machine running `python run.py`.
2. **Local web UI** (`webui.html`) — same constraint: localhost WebSocket only.
3. **Email channel** — works from anywhere, but turn-around is too slow for
   conversational use; latency is bound by the 60-second inbox poll.

The gap: there is no low-latency way to chat with curunir from a phone
while away from the machine that runs it. The original framing for this
spec was "useful while commuting or exercising." Voice is a natural
follow-on, but is **out of scope here** and gets its own spec; designing
voice in now would distort the portal architecture.

A second goal: enable a small number of trusted testers to each run their
own curunir and reach it through one shared portal — without forcing a
multi-tenant refactor inside curunir itself.

## Architecture

A federated model. Each user runs their own single-tenant curunir
container (anywhere — laptop, NUC, home server). A central portal
hosted on Render acts as a thin authenticated routing layer between
browsers and containers.

```
                  Browser (phone or desktop)
                         │
                         │  HTTPS  (static HTML)
                         │  WSS    /ws/browser
                         │  POST   /sign-in
                         ▼
         ┌──────────────────────────────────────┐
         │      Portal (FastAPI on Render)      │
         │                                      │
         │   Auth + admin + static + routing    │
         │            │              │           │
         │            ▼              ▼           │
         │   ┌─────────────┐   ┌──────────────┐ │
         │   │ Postgres    │   │ Email        │ │
         │   │ (Render     │   │ (Postmark or │ │
         │   │ add-on)     │   │  Resend)     │ │
         │   └─────────────┘   └──────────────┘ │
         │                                      │
         │           WSS  /ws/agent             │
         └────────────────────▲─────────────────┘
                              │
                              │  outbound, container-initiated
                              │  authenticated via shared-secret token
                              │
         ┌────────────────────┴─────────────────┐
         │     User's curunir Docker container  │
         │     (laptop / NUC / wherever)        │
         │                                      │
         │     PortalChannel (new)              │
         └──────────────────────────────────────┘
```

### Three components

1. **Portal app** (new) — FastAPI service on Render. Stateless except for
   Postgres (users + tokens) and an in-memory routing table mapping
   `user_id → {agent socket, browser sockets}`. Stores no chat content.
2. **Curunir container** (existing, plus one new channel) — `PortalChannel`
   is added; `WebSocketChannel` and `EmailChannel` keep working as-is.
3. **Browser frontend** (new) — single static HTML+JS bundle served by
   the portal at `/`.

### Invariants

- Container is the source of truth for everything user-related: identity,
  memory, schedules, attachments, conversation history.
- Portal stores only: account rows (email, allowlist), tokens (sign-in
  + container shared secret), `is_active` flag. **No chat content ever.**
- Conversation history shown in the browser comes from the container's
  in-memory `Agent.history`, requested over the wire on each browser
  connect.
- One container per user is active at a time. A second container with
  the same token kicks the first off.
- Multiple browser sessions per user are fine; portal fans out outbound
  messages to all of them.

## Authentication

### Admin-issued, reusable, non-expiring sign-in links

The portal has no public signup. The flow is:

1. **Admin creates user** via the admin UI or CLI. Inputs: email.
   Portal generates two random 32-byte tokens (URL-safe base64): a
   `sign_in_token` and a `container_token`. Both are stored on the
   `users` row.
2. **Portal sends one email** to the user with a sign-in link:
   `https://portal.example/sign-in?token=<sign_in_token>`.
3. **User clicks the link.** `GET /sign-in` does **not** sign them in
   directly — it renders a small confirmation page ("Sign in as
   alice@example.com? [Sign in]") with a CSRF-protected POST form.
   Submitting the form (`POST /sign-in` with the token in the request
   body) validates the token, sets a signed session cookie, and 302s
   to `/`. The GET-then-POST split keeps the token out of the
   `Referer` header to any post-sign-in third party (CDNs, etc.) and
   prevents link-preview bots (Slack, iMessage) from inadvertently
   "consuming" or logging an authenticated response — bots only ever
   see the static confirmation page.
4. **The cookie does not expire.** The browser stays signed in
   indefinitely.
5. **The sign-in link is reusable.** Clicking it on a new device any
   time later signs that device in too. The email *is* the credential.

The portal must configure access-log redaction so that `token=` query
parameters on `/sign-in` never reach persistent logs (Render's HTTP
access logs, app-level request logging). The token is the credential;
treat it the same as a password in logging discipline.

### Cookie format

Stateless signed payload — no sessions table:

```json
{"user_id": 42, "v": 1}
```

Signed with `PORTAL_SECRET_KEY` via `itsdangerous` (or stdlib `hmac`).
On every authenticated request, the server verifies the signature *and*
checks `users.is_active` for that `user_id`. Deactivated users are
kicked out on their next request.

The cookie is set with `Secure; HttpOnly; SameSite=Strict; Path=/`.
`SameSite=Strict` is load-bearing for two reasons: it prevents CSRF
on admin POSTs (see "Admin authorization" below) and it prevents the
session cookie from riding cross-site WebSocket upgrades, which
mitigates CSWSH against `/ws/browser`.

### Revocation

- **Sign-in token compromised** → admin clears or rotates
  `users.sign_in_token`. Old token instantly stops working; admin
  reissues by sending a new sign-in email.
- **Container token compromised** → admin clears or rotates
  `users.container_token`. Container's WebSocket gets dropped on next
  message; user updates env and restarts container.
- **User offboarded** → admin sets `is_active = false`. Cookie auth
  starts failing immediately. Container connection refused on next
  message.

### Rate limiting

`/sign-in?token=…` has an in-memory rate limit (10/min/IP). Tokens are
32 random bytes so brute force is impractical, but the limit costs
nothing.

### Admin authorization

Admin endpoints (`/admin/*`) are gated by `ADMIN_EMAILS` env var
(comma-separated). A signed-in user whose email is in the list sees
the admin UI; everyone else gets 403. No `is_admin` schema column.
Email comparison is normalized (lowercased, trimmed) on both sides.

State-changing admin endpoints (`POST /admin/users`,
`POST /admin/users/<id>/deactivate`,
`POST /admin/users/<id>/regenerate-tokens`,
`POST /admin/users/<id>/send-signin-email`) require a CSRF token
(synchronizer-token pattern: token issued in the `/admin` HTML and
checked on POST). `SameSite=Strict` on the session cookie is the
primary defense; the explicit CSRF token is defense-in-depth in case
a future browser quirk or proxy strips the SameSite enforcement.

## Data model

One Postgres table:

```sql
CREATE TABLE users (
  id              BIGSERIAL PRIMARY KEY,
  email           TEXT NOT NULL UNIQUE,
  sign_in_token   TEXT NOT NULL UNIQUE,
  container_token TEXT NOT NULL UNIQUE,
  is_active       BOOLEAN NOT NULL DEFAULT TRUE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX users_sign_in_token_idx   ON users (sign_in_token)   WHERE is_active;
CREATE INDEX users_container_token_idx ON users (container_token) WHERE is_active;
```

Both token columns are independently rotatable. A separate `containers`
table (to allow N containers per user) is **out of scope** — added if
the need actually shows up.

## Container ↔ portal protocol

### Connection

Container dials `wss://portal.example/ws/agent` and presents the
container token in an `Authorization: Bearer <container_token>`
header on the WebSocket upgrade request. The token is **not** carried
in the URL query string — query strings are written to HTTP access
logs at the edge, and the container token is non-expiring.

Portal validates the token against the `users` table, looks up
`user_id`, kicks any prior `agent_ws` for that user, then registers
the new socket.

Invalid or inactive tokens close the socket with code 4003 ("forbidden").

### Wire format

The protocol reuses the existing `IncomingMessage` / `OutgoingMessage`
JSON envelopes from `src/channels/ws.py`, wrapped in a thin `type`
discriminator since the channel now also carries control messages:

**Portal → container:**
```json
{ "type": "user_message",
  "payload": { "content": "...", "command": null, "attachments": [...] } }

{ "type": "history_request" }
```

**Container → portal:**
```json
{ "type": "agent_message",
  "payload": { "content": "...", "tool_calls": [...], "final": true,
               "delta": false, "attachments": [...],
               "workflow": {...}, "stats": {...} } }

{ "type": "history_snapshot",
  "messages": [
    {"role": "user", "content": "...", "attachments": [...]},
    {"role": "assistant", "content": "...", "tool_calls": ["bash: ls"],
     "attachments": [...], "workflow": {...}},
    ...
  ] }
```

The `payload` object inside `user_message` and `agent_message` is
bit-for-bit the same as the existing `ws.py` JSON envelope. This is
deliberate: `PortalChannel` and `WebSocketChannel` produce identical
envelopes, and the shared attachment helpers handle the bytes the
same way.

### History snapshot semantics

`history_snapshot.messages` is **not** the raw `Agent.history` (which
is LiteLLM-format and contains tool-internal noise). It is a
chat-shaped projection produced on the curunir side:

- User turns: `{role, content, attachments}`.
- Assistant final turns: `{role, content, tool_calls, attachments, workflow}`.
- Tool internals are summarized (e.g. `"bash: ls -la"`) — full traces
  stay inside `Agent.history`.

Producing the projection is a small helper that walks `Agent.history`
once. The size cap is 200 messages or 100 KB serialized, whichever
hits first; older content is truncated with a sentinel
`{"role": "system", "content": "...truncated..."}` at the top.

### Attachments

Same caps and validation as `WebSocketChannel`: 5 MB image, 10 MB PDF,
256 KB text, 20 MB total batch. The validation helpers
(`_decode_attachments`, `_enrich_attachments`, `_stage_attachments`,
`_unique_filename`) are extracted from `src/channels/ws.py` into a
shared module `src/channels/_attachments.py` and imported by both
channels.

The session ID for portal-originated messages is a stable per-user
value: `f"portal-{user_id}"`. This keeps memory extraction and
schedules cleanly partitioned from the local CLI session
(`SESSION_ID = "cli"`).

## Browser ↔ portal protocol

Browser opens `wss://portal.example/ws/browser`. The session cookie is
sent automatically on the upgrade request. Portal validates the
cookie, looks up `user_id`, and adds the socket to `browser_wss`.

The upgrade handler additionally verifies that the `Origin` header
matches `PORTAL_BASE_URL`. WebSocket upgrade requests are not subject
to the Same-Origin Policy and are not constrained by CORS, so without
an explicit `Origin` check any site a logged-in user visits could open
a WS to `/ws/browser` and ride the session cookie — full agent
takeover (read history, drive `bash`/`write`/`edit`/`delegate` tools).
`SameSite=Strict` on the session cookie is the primary defense; the
`Origin` check is defense-in-depth and a hard requirement.

Browser sees the **unwrapped** envelopes — no `type` discriminator —
because it only ever receives agent messages and history snapshots.
Portal does the unwrapping. From the browser's perspective:

- **Browser → portal:** existing `IncomingMessage`-shaped JSON
  (identical to what `cli.py` sends to `ws.py` today).
- **Portal → browser:** existing `OutgoingMessage`-shaped JSON, plus
  one initial `{"type": "history_snapshot", "messages": [...]}` on
  connect, plus periodic `{"type": "agent_status",
  "status": "online"|"offline"}` events.

This is intentional: browser-side JS is ~95% the same as the
corresponding code in `webui.html`.

## In-memory routing (portal)

```python
@dataclass
class UserRoute:
    agent_ws: WebSocket | None
    browser_wss: list[WebSocket]

routing: dict[int, UserRoute] = {}
```

Single-process. Fine on Render's smaller plans. If the portal ever
scales to multiple instances, this needs Redis pub/sub — **out of
scope** until it matters.

### Lifecycle

| Event | Behavior |
|---|---|
| Container connects with valid token | Kick prior `agent_ws` if any (close with code 4002 "replaced"); register new; broadcast `agent_status: online` to attached browsers |
| Container connects with invalid/inactive token | Close with code 4003 |
| Container disconnects | Clear `agent_ws`; broadcast `agent_status: offline` |
| Browser connects with valid cookie | Add to `browser_wss`; if `agent_ws` exists, send `history_request` to container, forward snapshot back to this browser; send `agent_status` |
| Browser connects with invalid cookie | Close with code 4003 |
| Browser sends `user_message`, agent connected | Wrap and forward to `agent_ws` |
| Browser sends `user_message`, no agent connected | Reply directly to *this* browser with synthetic `{"content": "Agent offline.", "final": true}`. Do not store. Other browsers do not see it. |
| Agent message arrives, browsers connected | Fan out to all `browser_wss` |
| Agent message arrives, no browsers connected | Drop at portal (logged at INFO). Container's `Agent.history` retains it; next browser connect recovers via snapshot. |
| User deactivated mid-session | Next message on either socket → close with code 4001 "inactive" |

## Frontend

### Pages

1. **`GET /sign-in?token=…`** — Validates the token shape and looks
   up the matching active user. On match, renders a small
   confirmation page ("Sign in as alice@example.com? [Sign in]")
   containing a POST form that resubmits the token in the request
   body. On failure: 1-page "Sign-in link is invalid. Contact admin."
   No signup form, no "request a new link" button. Response is sent
   with `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.
2. **`POST /sign-in`** — Validates the token from the body, sets the
   signed session cookie, 302 → `/`.
3. **`GET /`** — Chat surface. Requires valid session cookie; otherwise
   302 → static `/needs-invite.html` page.
4. **`GET /admin`** — Gated by `ADMIN_EMAILS` env var. Lists users,
   "create user" form (email field; container token displayed once on
   creation; "send sign-in email" button), deactivate / regenerate-tokens
   buttons. All state-changing actions are POSTs carrying a CSRF
   token issued in this page's HTML. Ugly is acceptable for v1.

### Layout

Single chat surface, scales from phone to desktop. Mobile fills the
viewport; desktop centers a max-width-720px column.

```
Mobile (≤640px):                   Desktop (>640px):

┌──────────────────────┐           ┌────────────────────────────────────┐
│ ● agent online       │           │           ● agent online           │
├──────────────────────┤           ├────────────────────────────────────┤
│  > what's the…       │           │       ┌──────────────────┐         │
│  Sure, here's…       │           │       │  > what's the…   │         │
│                      │           │       │  Sure, here's…   │         │
├──────────────────────┤           │       └──────────────────┘         │
│ 📎 Type a message ▶ │           ├────────────────────────────────────┤
└──────────────────────┘           │       📎 Type a message      ▶  │
                                   └────────────────────────────────────┘
```

- Header: thin strip with status pill
  (`● agent online` / `● agent offline` / `↻ reconnecting…`).
- Body: scrolling message list. User turns visually distinct from
  assistant turns. Tool-call summaries as muted single lines.
- Footer: attach button + auto-growing text input + send.
- **Excluded by design**: no sidebar, no thread switcher, no settings
  page, no theme switcher, no artifact pane, no workflow indicator,
  no message search, no export, no command palette.

### Behavior

**On page load (authenticated):**
1. Render shell.
2. Open WebSocket to `/ws/browser` (cookie auto-attaches).
3. Wait for `history_snapshot`, render messages.
4. Show status pill from `agent_status` events.

**On user submits a message:**
- Validate attachment caps client-side (mirror server caps).
- Base64-encode attachments, send `{content, attachments}` over WS.
- Optimistically append user message to chat.
- If `agent_status === "offline"`, show inline "Agent is offline" and
  do not send.

**On agent message arrives:**
- If `delta: true`, append to in-progress assistant bubble.
- If `tool_calls`, render as muted summary lines beneath the
  in-progress assistant bubble.
- If `final: true`, finalize.

### Tech choices

- **Vanilla JS, no build step.** Same model as `webui.html`.
- **`marked` + `highlight.js` via CDN.**
- **CSS handwritten in a `<style>` block.**
- **Mobile viewport:**
  `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">`,
  `100dvh` instead of `100vh`, tap targets ≥44px.
- **No PWA / service worker in v1.** Add when voice arrives.

## Reconnect, errors, observability

### Container side (`PortalChannel`)

- On startup: if `CURUNIR_PORTAL_URL` and `CURUNIR_PORTAL_TOKEN` are
  both set, dial portal. Otherwise the channel is inert.
- **Reconnect:** exponential backoff 1s → 2s → 4s → 8s → 16s → cap
  30s, with ±20% jitter. Single in-flight attempt at a time
  (`asyncio.Lock`).
- **Auth failure (4003)** is terminal: log error, do not retry, leave
  `EmailChannel`/`WebSocketChannel` running. The container is alive;
  only the portal channel is unavailable until admin acts.
- **Replaced (4002)**: do not retry — another container won the
  registration. Log warning.
- **Network / 5xx / connection close**: retry with backoff.
- On reconnect, the container does *not* replay anything. Browsers see
  current state via fresh `history_snapshot` after they reconnect.

### Portal side

- **Logging:** every connection lifecycle event tagged with `user_id`
  (or "anonymous" pre-auth): agent connected/disconnected, browser
  connected/disconnected, message routed, message dropped (no agent /
  no browsers). Counters logged hourly at INFO.
- **`/healthz`:** returns 200 + `SELECT 1` against Postgres. Render's
  health check polls this.
- **No metrics backend in v1.** Logs are the observability surface.
  Add Prometheus / OTEL when the portal has more than one instance.

### Browser side

- WS reconnect with backoff (1s → 30s, jitter), status pill reflects
  state.
- On WS open: render existing local message list optimistically until
  first server message arrives (handles transient blips without UI
  flicker).
- On send failure (no agent): synthetic "Agent offline" inline.
- On 401 from any HTTP request: reload page, which redirects to
  `/needs-invite`.

## Testing

Mirror existing `tests/` style: async, `pytest-asyncio`, fixtures for
DB and app.

### Unit

- Cookie sign / verify (valid, tampered, deactivated user).
- Sign-in token validation (valid, unknown, deactivated).
- Wire envelope encode / decode (round-trip both directions).
- Routing table operations (register, kick, fan-out, drop).
- History snapshot projection from a synthetic `Agent.history`.
- Attachment helpers (already tested in `tests/test_channels.py`;
  reuse those tests after extraction to `_attachments.py`).
- Cookie attributes on the sign-in response (`Secure`, `HttpOnly`,
  `SameSite=Strict`).
- `/ws/browser` rejects upgrade with mismatched / missing `Origin`.
- `/ws/agent` rejects upgrade when the `Authorization` header is
  missing or carries an invalid / inactive token; accepts when valid.
- Admin POST endpoints reject requests without a valid CSRF token.

### Integration

- Spin up FastAPI app via `httpx.AsyncClient` + `websockets` test
  client. Mock agent socket. Verify:
  - Sign-in flow end-to-end (POST → cookie → WS connect).
  - User message round-trip (browser → portal → mock agent → portal →
    browser).
  - History request on browser connect.
  - Agent kick when second container connects.
  - Browser kicked when `is_active` flipped mid-session.
  - "Agent offline" synthetic when no agent connected.

### Curunir-side

- `tests/test_portal_channel.py` — `PortalChannel` against a mock
  portal endpoint. Verify reconnect backoff, history snapshot
  serialization, attachment passthrough, terminal vs. retryable error
  codes.

### Manual

- Local: portal runs on `localhost:8000`, container on the same
  machine, browser on `localhost`. End-to-end smoke before deploy.
- Render staging: deploy to a Render preview environment, point a
  real container at it via `CURUNIR_PORTAL_URL`, verify from a phone.

## File-level changes

### New: portal service (separate Python project, same repo)

- `portal/pyproject.toml` — FastAPI, uvicorn, asyncpg, itsdangerous,
  postmark or resend client.
- `portal/Dockerfile`, `portal/render.yaml` — Render deployment.
- `portal/app.py` — FastAPI app entrypoint.
- `portal/config.py` — env config: `DATABASE_URL`, `PORTAL_SECRET_KEY`,
  `EMAIL_API_KEY`, `EMAIL_FROM`, `ADMIN_EMAILS`, `PORTAL_BASE_URL`.
- `portal/db.py` — asyncpg pool, `users` table accessors.
- `portal/auth.py` — `/sign-in` endpoint, cookie sign / verify,
  dependency injection for "current user".
- `portal/admin.py` — `/admin` endpoints + `python -m portal.admin
  create-user --email …` CLI.
- `portal/email_send.py` — single `send_signin_email(email, link)`
  function, abstracted over Postmark or Resend.
- `portal/routing.py` — in-memory `UserRoute` table, lifecycle
  methods.
- `portal/ws_agent.py` — `/ws/agent` endpoint.
- `portal/ws_browser.py` — `/ws/browser` endpoint.
- `portal/static/index.html` — chat surface (single HTML+JS+CSS file).
- `portal/static/sign-in-error.html`, `portal/static/needs-invite.html`
  — small static stubs.
- `portal/static/admin.html` — minimal admin UI.
- `portal/migrations/0001_create_users.sql` — schema.
- `portal/tests/` — pytest suite (mirrors `tests/` structure).

### New: curunir-side

- `src/channels/_attachments.py` — extracted helpers:
  `_decode_attachments`, `_enrich_attachments`, `_stage_attachments`,
  `_unique_filename`. Uses `_normalize_unicode_whitespace` from
  `email.py` (existing dependency).
- `src/channels/portal.py` — `PortalChannel` implementing the
  `Channel` protocol. Dials out, reconnects with backoff, serializes
  `history_snapshot` from `Agent.history`.
- `tests/test_portal_channel.py`.

### Modified

- `src/channels/ws.py` — replace local helper definitions with
  imports from `_attachments.py`.
- `run.py` — instantiate `PortalChannel` when `CURUNIR_PORTAL_URL` and
  `CURUNIR_PORTAL_TOKEN` are both set, add to channel TaskGroup, wire
  to `out_queue` router.
- `.env.example` — document `CURUNIR_PORTAL_URL`,
  `CURUNIR_PORTAL_TOKEN`.
- `CLAUDE.md` — add Portal channel to the Channels section, add
  `portal/` to the directory tour.

## Out of scope

Each is potentially valuable, none are needed for v1. Listed so they
do not creep in:

- **Voice.** Real-time STT/TTS, push-to-talk, audio streaming. Will
  be its own spec, layered on this portal.
- **Multiple containers per user.** One active container per user;
  second one kicks the first.
- **Multi-process portal.** Single instance; routing is in-process.
- **Conversation persistence on the portal.** Portal stores no chat
  content. History lives in the container's `Agent.history`.
- **Threads / multi-conversation UI.** One live conversation per user.
- **Mobile PWA / offline support.** Plain web page in v1.
- **Message search, export, threading, file browser, settings page,
  artifact pane, workflow indicator.**
- **Billing, quotas, rate limits beyond brute-force protection on
  `/sign-in`.**
- **Self-service signup** of any kind.
- **`webui.html` consolidation.** The local desktop UI keeps existing
  as-is; the portal is a separate, hosted surface.
