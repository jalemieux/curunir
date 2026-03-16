# WebSocket CLI Channel Design

## Problem

The CLI channel is coupled to the agent process — it reads stdin and writes stdout directly. When the agent runs in a Docker container, there's no way to interact with it from the host. The CLI needs to be a separate client that connects over a network boundary.

## Solution

Replace the in-process `CLIChannel` with a `WebSocketChannel` on the server side and a standalone `cli.py` client. The WebSocket channel implements the same `Channel` protocol (`start()` + `send()`), slots into the existing queue-based architecture, and is always on. The client owns all Rich terminal UI.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Agent Process (run.py)                             │
│                                                     │
│  cli.py ──WS──→ WebSocketChannel ──→ in_queue       │
│                                                     │
│                  out_queue ──→ Router                │
│                                  ├──→ WebSocketChannel ──WS──→ cli.py
│                                  ╰──→ EmailChannel   │
└─────────────────────────────────────────────────────┘
```

## Components

### 1. `src/channels/ws.py` — WebSocketChannel

Implements the `Channel` protocol. Uses the `websockets` library.

**`start()`:**
- Creates a `websockets.serve()` server bound to `{host}:{port}` (default `0.0.0.0:8765`)
- Enters the async context manager, then awaits a `Future` that never completes (blocks until cancelled)
- On cancellation (e.g., `TaskGroup` teardown, KeyboardInterrupt): closes active connection if any, then closes the server. This prevents leaked sockets blocking Docker container restarts.
- Accepts one connection at a time; rejects additional connections
- On connection: reads JSON messages from the client, constructs `IncomingMessage` (adding `channel="cli"`, `session_id="cli"`, `reply_address={}`), pushes to `in_queue`
- On client disconnect: enqueues `IncomingMessage(command="extract")` to trigger memory extraction, then logs and keeps listening for reconnect. Agent continues running — no session loss. The `session_id` is always `"cli"` (constant, not derived from the connection), so reconnection preserves conversation history.

**`send(msg: OutgoingMessage)`:**
- Serializes message to JSON, sends over WebSocket
- `attachments` field: sent as `null` when empty (never `[]`)
- If no client connected, logs warning and drops the message

**Wire protocol — JSON over WebSocket:**

Client → Server:
```json
{"content": "hello", "command": null}
```

Server → Client:
```json
{
  "content": "Here's what I found...",
  "tool_calls": ["Read src/foo.py", "Grep 'def handle'"],
  "final": true,
  "attachments": null
}
```

The server owns `channel`, `session_id`, and `reply_address` — the client doesn't set or see these. The client sends user content and optional commands; the server wraps them into `IncomingMessage`. The `attachments` field on `IncomingMessage` is not supported over the wire protocol (reserved for future use).

### 2. `cli.py` — Standalone CLI Client

New file at repo root. Entrypoint: `python cli.py [--host localhost] [--port 8765]`.

**Input loop:**
- Reads from stdin via Rich `Console.input()`
- Runs in executor to avoid blocking the async event loop
- Sends JSON over WebSocket: `{"content": "...", "command": null}`
- `/clear`: sends `{"content": "", "command": "clear"}`
- `/verbose`: handled locally (toggles tool call display), not sent to server. Tool calls are hidden by default to reduce noise over the wire; `/verbose` enables them.
- EOF (Ctrl-D): closes WebSocket connection cleanly, exits. The server detects the close and enqueues an `extract` command to trigger memory extraction.
- Gated by an `asyncio.Event` — waits for `final: true` before accepting next input

**Output loop:**
- Receives JSON from WebSocket
- Renders tool calls as tree-formatted lines (├─, ╰─) when verbose
- Renders agent response as Rich markdown
- Shows attachment filenames
- Manages spinner (start on send, stop on receive)
- Sets ready event when `final: true`

**Reconnection:**
- On disconnect: stops spinner, resets input gate, prints status, attempts reconnect with exponential backoff
- On connection failure at startup: retries with clear error message
- On server-initiated close (e.g., `docker compose down`): same reconnect behavior. The client retries indefinitely — the user can Ctrl-C to exit.

### 3. Files Removed

- `src/channels/cli.py` — replaced entirely by `ws.py` + `cli.py`

### 4. Files Modified

**`src/config.py`:**
- Remove `CLI_ENABLED` support

**`run.py`** (config loading):
- Load `WS_HOST` (default `"0.0.0.0"`) and `WS_PORT` (default `8765`) from env vars in `run.py` and pass to `WebSocketChannel` constructor, following the same pattern as email config loading

**`run.py`:**
- Replace `CLIChannel` instantiation with `WebSocketChannel`
- Remove `CLI_ENABLED` conditional — WebSocket channel is always started
- WebSocket channel registered as `"cli"` in the channels dict (keeps routing compatible)

**`requirements.txt`:**
- Add `websockets`

**`Dockerfile`:**
- Add `EXPOSE 8765`

**`docker-compose.yml`:**
- Add port mapping: `"8765:8765"`

### 5. Tests

**New tests (`tests/test_ws_channel.py`):**
- WebSocketChannel accepts connection and forwards messages to in_queue
- WebSocketChannel.send() delivers messages to connected client
- WebSocketChannel.send() drops messages when no client connected (logs warning)
- WebSocketChannel rejects second concurrent connection
- Client reconnects after server-side disconnect

**Updated tests (`tests/test_channels.py`):**
- Remove CLIChannel-specific tests
- Keep shared Channel protocol tests, apply to WebSocketChannel

**New tests (`tests/test_cli_client.py`):**
- Input loop sends correct JSON for normal messages and commands
- Output loop renders tool calls, markdown, attachments
- `/verbose` toggles tool call display locally
- Reconnection on disconnect
- Tests use a real `websockets` test server (lightweight, in-process) rather than mocking the connection

## Running

**Local dev (two terminals):**
```bash
# Terminal 1: start agent
python run.py

# Terminal 2: connect CLI
python cli.py
```

**Docker:**
```bash
# Start agent in container
docker compose up -d

# Connect from host
python cli.py --host localhost --port 8765
```

## Non-Goals

- Multi-client support (one connection at a time)
- Authentication (trusted network assumption, same as current stdin)
- TLS (add later if needed, or terminate at a reverse proxy)
- Web UI (WebSocket protocol is compatible, but no browser client in scope)
