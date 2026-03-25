# Web UI Design

A browser-based chat interface that replicates all `cli.py` functionality and adds an artifact viewer for long-form markdown content.

## Reference Mockup

See `.superpowers/brainstorm/51880-1774394788/final-reference.html` — open in a browser for the definitive visual reference. Screenshot of the earlier iteration was captured by the user during brainstorming.

## Architecture

Single static HTML file (`webui.html`) in the repo root. No build step, no server-side framework. Connects directly to the existing Curunir WebSocket server using the same JSON protocol as `cli.py`.

External dependencies loaded via CDN `<script>` tags:
- `marked` (~8kb gzipped) — markdown parsing
- `highlight.js` (~30kb gzipped) — syntax highlighting for code blocks

### Server-Side Changes

Five changes to support the web UI:

1. **`src/channels/base.py`** — Add `workflow: dict | None = None` field to `OutgoingMessage`.
2. **`run.py` `agent_worker()`** — After `agent.handle()` returns, enrich attachments before constructing `OutgoingMessage`: for each attachment whose `mime_type` starts with `text/` or is `application/json`, read the file content and add it as a `content` field. Normalize the `path` to be relative to the working directory (strip the project root prefix). Cap content inclusion at 512KB per file — larger files get `content: null`. If the file no longer exists at send time, set `content: null` and include `"error": "file not found"`.
3. **`src/channels/ws.py` `send()`** — Pass through the `workflow` field from `OutgoingMessage` to the JSON payload (one line).
4. **`cli.py`** — Update attachment display to handle the new `content` field. When an attachment includes content, render the markdown inline or show a richer summary beyond the current filename/path icon line.
5. **`run.py` + `base.py`** — Workflow plumbing: `agent.handle()` currently returns a plain `str`. To propagate workflow state, add a `metadata: dict` accumulator (same pattern as the `attachments` list) that is passed into `agent.handle()` and populated by skills/tools during execution. The agent worker reads `metadata.get("workflow")` and passes it to the `OutgoingMessage` constructor. This is a new mechanism — there is no existing callback for arbitrary metadata.

No new servers, no new endpoints, no new Python dependencies.

### Message Model

The server sends discrete JSON messages over WebSocket — there is no token-level streaming. Each `on_tool_call` fires one message (`final: false`), and the final response is a single message (`final: true`). The web UI does not need streaming infrastructure.

CDN scripts (`marked`, `highlight.js`) require an internet connection. The HTML file will not render markdown correctly when opened offline.

## Layout

Full viewport height, three regions:

```
┌─────────────────────┬──┬─────────────────────┐
│                     │  │  Directory tree      │
│   Chat pane         │◄►│─────────────────────│
│   (terminal mirror) │  │  Rendered artifact   │
│                     │  │                      │
├─────────────────────┴──┴─────────────────────┤
│  ● requirements → plan → [design] → implement │
└───────────────────────────────────────────────┘
```

- **Left pane:** Terminal-mirror chat (50% width default)
- **Resize handle:** Draggable divider between panes
- **Right pane:** Artifact viewer — directory tree on top, rendered content below
- **Bottom bar:** Workflow status, full width

## Chat Pane (Left)

### Header
- "Curunir" title in bold
- Model name and WS URI in dim text (populated from the welcome message)
- Connection status indicator

### Message Stream
Scrollable area, auto-scrolls to bottom on new content. Message types:

| Type | Rendering |
|------|-----------|
| User input | Green `>` prompt, green text |
| Tool calls | Dim `├─`/`╯─` tree connectors with tool name and args. Hidden when verbose mode is off. All tool calls initially render with `├─`. When the next non-tool-call message arrives (content or `final: true`), the last tool call's connector is retroactively updated to `╯─`. |
| Assistant response | Markdown rendered via `marked`. Monospace font for inline code, sans-serif for prose within rendered blocks. |
| Attachments | Clickable `📄 filename.md` chip below the message. Clicking selects the artifact in the right pane and scrolls the tree to it. |
| Spinner | Pulsing "Thinking..." in cyan. Shown after sending a message, hidden on first response data. |
| System messages | Yellow text for disconnect/reconnect notices. |

### Input Bar
Fixed at bottom of the chat pane:
- Green `>` prompt
- Text input, monospace font, submit on Enter
- Caret color matches the green prompt

### Input Gating
The input is disabled (visually dimmed, ignores Enter) while a response is in progress (`final: false` messages streaming in). Re-enabled when `final: true` is received. Same semantics as `cli.py`'s `ready` event.

### Commands
Handled client-side, same behavior as `cli.py`:
- `/clear` and `/new` — send `{"content": "", "command": "clear"}` to server. Clear chat history and artifact cache in the UI immediately on submission (don't wait for server confirmation — the server responds with an empty `final: true` message which simply re-enables input).
- `/verbose` — toggle tool call visibility, show status message

## Artifact Pane (Right)

### Directory Tree (Top Section)
Built dynamically from attachment paths received over the WebSocket connection.

- **Root label:** working directory basename in dim uppercase (e.g., `workspace/`)
- **Folders:** Yellow text, collapsible with `▸` (collapsed) / `▾` (expanded) toggles. Collapsed folders show a dim badge with the count of direct child files (not recursive).
- **Files:** Dim text, clickable. Active file highlighted with indigo left border and indigo text.
- **Behavior:** New artifacts auto-select and scroll the tree into view. Tree structure derived from the `path` field of attachments (relative paths, normalized by the server) — directories are inferred from path segments.

### Rendered Content (Bottom Section)
Displays the selected artifact's content, rendered from markdown to HTML via `marked`:

- Sans-serif font for body text (system font stack)
- Monospace for code blocks with syntax highlighting via `highlight.js`
- Full support for: headers, paragraphs, lists, tables, code blocks (fenced + indented), bold, italic, links, images
- Scrollable independently from the tree

### Content Source
The WebSocket channel's `send()` method reads file content for text-based attachments and includes it in the payload:

```json
{
  "attachments": [
    {
      "filename": "competitor-analysis.md",
      "path": "workspace/research/competitor-analysis.md",
      "mime_type": "text/markdown",
      "size": 2048,
      "content": "# Competitor Analysis\n\nThree main competitors..."
    }
  ]
}
```

The web UI caches artifact content in an in-memory JS map (`path → content`). Content is replaced if the same path is attached again (artifact update).

### Empty State
When no artifacts exist, the right pane shows a centered dim placeholder message.

### Divider
The boundary between directory tree and rendered content is a horizontal line. The tree section has a default height of ~30% of the right pane and grows with content up to a maximum of 40% of the right pane height, after which it scrolls internally.

### Resize Handle (Left/Right)
The vertical divider between chat and artifact panes is draggable. Minimum width for either pane is 300px. Resize state is ephemeral — resets to 50/50 on page reload (consistent with the "no local storage persistence" scope exclusion).

## Workflow Bar (Bottom)

Spans full width below both panes. Displays the agent's declared workflow progress.

### Protocol
Outgoing messages may include a `workflow` field:

```json
{
  "content": "...",
  "workflow": {
    "steps": ["requirements", "plan", "design", "implement", "review"],
    "current": "design"
  }
}
```

### Rendering
- Green dot indicator at the left
- Steps displayed left-to-right, separated by dim `→` arrows
- Completed steps (before current): green text
- Current step: indigo, bold, bottom border (not text-decoration underline)
- Future steps: dim/gray text
- Hidden entirely when no workflow has been declared

### Behavior
- Updated whenever a message includes a `workflow` field
- Persists across messages (last declared workflow remains visible)
- Cleared on `/clear` or `/new`

## Connection Management

Same reconnection logic as `cli.py`:

1. **Initial connection:** connect to `ws://host:port`, display model info from welcome message
2. **Exponential backoff:** on disconnect, retry at 1s, 2s, 4s, 8s, ... up to 30s max
3. **Status messages:** yellow "Disconnected from ws://... Reconnecting..." and green "Reconnected" in the chat stream
4. **Pending payload retry:** if a send fails due to connection drop, the payload is saved and retried on the next successful connection
5. **Header indicator:** connection status reflected in the header (connected/reconnecting)

## Configuration

The web UI accepts connection parameters via URL query string:

```
webui.html?host=localhost&port=8765
```

Defaults: `host=localhost`, `port=8765` (same as `cli.py`).

## Visual Style

Terminal-mirror aesthetic throughout:

- **Background:** `#0a0a12` (near-black with slight blue)
- **Font:** monospace stack — SF Mono, Fira Code, Cascadia Code, JetBrains Mono
- **Color palette:**
  - Green `#4ade80` — user input, prompts, completed workflow steps
  - Indigo `#6366f1` — active artifact, current workflow step, attachment chips
  - Cyan `#06b6d4` — thinking spinner
  - Yellow `#e2b55a` — folder names
  - Dim grays `#333`–`#555` — connectors, metadata, inactive elements
  - White `#e0e0e0`–`#fff` — assistant text, headings
- **Borders:** `#1a1a22` throughout
- **Artifact content:** uses system sans-serif font stack for readability, monospace only for code

## File Location

`webui.html` in the repo root, alongside `cli.py`. Single file containing all HTML, CSS, and JavaScript.

## Scope Exclusions

- No authentication or access control (same as current WS server)
- No file upload from browser
- No editing artifacts in the browser
- No mobile-responsive layout (desktop-first, like the CLI)
- No local storage persistence across page reloads
