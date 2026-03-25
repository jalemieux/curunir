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

Four small changes to support the web UI:

1. **`src/channels/base.py`** — Add `workflow: dict | None = None` field to `OutgoingMessage`
2. **`src/channels/ws.py` `send()`** — For each attachment with a text-based mime type, read the file content and include it as a `content` field in the attachment dict before serializing to JSON. Pass through the `workflow` field to the JSON payload.
3. **`cli.py`** — Update attachment display to handle the new `content` field. When an attachment includes content, render the markdown inline or show a richer summary beyond the current filename/path icon line.
4. **Agent/skills** — Skills that want to communicate workflow state include a `workflow` field in their outgoing messages via the existing callback mechanism.

No new servers, no new endpoints, no new Python dependencies.

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
| Tool calls | Dim `├─`/`╯─` tree connectors with tool name and args. Hidden when verbose mode is off. Last tool call uses `╯─`, others use `├─`. |
| Assistant response | Markdown rendered via `marked`. Monospace font for inline code, sans-serif for prose within rendered blocks. |
| Attachments | Clickable `📄 filename.md` chip below the message. Clicking selects the artifact in the right pane and scrolls the tree to it. |
| Spinner | Pulsing "Thinking..." in cyan. Shown after sending a message, hidden on first response data. |
| System messages | Yellow text for disconnect/reconnect notices. |

### Input Bar
Fixed at bottom of the chat pane:
- Green `>` prompt
- Text input, monospace font, submit on Enter
- Caret color matches the green prompt

### Commands
Handled client-side, same behavior as `cli.py`:
- `/clear` and `/new` — send `{"content": "", "command": "clear"}` to server, clear chat history and artifact cache in the UI
- `/verbose` — toggle tool call visibility, show status message

## Artifact Pane (Right)

### Directory Tree (Top Section)
Built dynamically from attachment paths received over the WebSocket connection.

- **Root label:** `workspace/` in dim uppercase
- **Folders:** Yellow text, collapsible with `▸` (collapsed) / `▾` (expanded) toggles. Collapsed folders show a dim file count badge.
- **Files:** Dim text, clickable. Active file highlighted with indigo left border and indigo text.
- **Behavior:** New artifacts auto-select and scroll the tree into view. Tree structure derived from the `path` field of attachments — directories are inferred from path segments.

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
The boundary between directory tree and rendered content is a horizontal line. The tree section has a sensible default height and can grow with content (up to a max).

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
- Current step: indigo, bold, underlined
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
