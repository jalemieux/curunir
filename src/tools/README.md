# Tools

The tools subsystem gives the agent the ability to interact with the filesystem, run commands, fetch web content, delegate work, and more. Tools are defined as JSON schemas, registered at import time, and dispatched to executor functions at runtime.

## File Layout

| File | Purpose |
|------|---------|
| `schemas.py` | Tool schema definitions and registry |
| `dispatcher.py` | Routes tool calls to executor functions |
| `fs_tools.py` | `glob`, `grep`, `read`, `edit`, `write` |
| `bash_tool.py` | `bash` — shell command execution |
| `web_fetch.py` | `web_fetch` — URL content extraction |
| `skill_tool.py` | `load_skill` — loads a skill's SKILL.md |
| `delegate.py` | `delegate` — spawns a sub-agent |
| `schedule_tool.py` | `schedule` — CRUD for cron tasks |
| `attach.py` | `attach` — attaches a file to the response |
| `to_audio.py` | `to_audio` — rewrites text for speech and attaches an MP3 (opt-in) |

## Schema Registry (`schemas.py`)

All tool schemas live in `schemas.py` as OpenAI-format function-calling dicts. At import time, each schema is passed to `_register()`, which stores it in `ALL_TOOL_SCHEMAS` (a name → schema dict) and appends the name to either `_DEFAULT_TOOL_NAMES` or `_OPT_IN_TOOL_NAMES`.

```python
_register(schema)                 # default tool — always available
_register(schema, opt_in=True)    # opt-in tool — only available when a skill requests it
```

**`get_tool_schemas(names)`** is the public API:
- `get_tool_schemas()` — returns all default tool schemas (excludes opt-in).
- `get_tool_schemas(["glob", "read", "attach"])` — returns exactly those tools from either pool.

### Default Tools

`glob`, `grep`, `read`, `edit`, `write`, `bash`, `load_skill`, `web_fetch`, `delegate`, `schedule`

### Opt-in Tools

`to_audio` — only available when a skill's frontmatter includes `tools: to_audio`.

## Dispatcher (`dispatcher.py`)

`execute_tool_call()` is the single async entry point called by the agent loop. It resolves the tool name and routes to the correct executor:

1. **Async executors** (e.g. `delegate`): looked up via `_get_native_async_executor()` and awaited directly. This uses lazy imports to avoid circular dependencies since `delegate` imports `Agent`.
2. **Sync executors** (everything else): looked up from the `_SYNC_EXECUTORS` dict and run via `asyncio.to_thread()` to avoid blocking the event loop.

Special-case handling:
- `attach` receives the mutable `attachments` list so it can append file metadata.
Unknown tool names return an `"Unknown tool: {name}"` error string.

## How the Agent Uses Tools

### Initialization

`Agent.__init__` accepts an optional `tools` parameter (a list of tool names). When `None`, all default tools are provided. Sub-agents get an explicit subset (everything except `delegate` to prevent recursive spawning).

### Schema Selection

`Agent._get_tool_schemas()` calls `get_tool_schemas(self.tools)` for the base set, then merges in any session-scoped opt-in tools that were unlocked by loading a skill.

### The Tool Loop

Inside `Agent.handle()`:

1. The LLM response is checked for `tool_calls`.
2. Each tool call is dispatched to `execute_tool_call()` sequentially.
3. After `load_skill` calls, the returned SKILL.md frontmatter is parsed for a `tools:` field. Any listed tools are added to `_session_tools[session_id]` and the schema list is refreshed.
4. Tool results are appended to history as `role: "tool"` messages.
5. History is trimmed and the loop continues until the LLM responds with text only (no tool calls) or the iteration limit is reached.

### Skill-Triggered Tool Unlock

When a skill's SKILL.md contains frontmatter like:

```yaml
---
name: reporter
tools: attach
---
```

Loading this skill via `load_skill` causes the agent to parse the frontmatter, find `tools: attach`, and add `attach` to the session's available tools. The tool schemas are refreshed so the LLM can see and call `attach` for the remainder of that session.

## Individual Tool Details

### File System Tools (`fs_tools.py`)

- **glob**: Uses Python's `glob.glob()` with `recursive=True`.
- **grep**: Prefers `ripgrep` (`rg`) when available; falls back to a pure-Python regex search. Supports `output_mode` (content / files_with_matches / count) and context lines.
- **read**: Returns numbered lines. Handles binary formats via `_BINARY_READERS`: PDF (pymupdf), DOCX (python-docx), XLSX (openpyxl), CSV. Images return a placeholder message.
- **edit**: Exact string replacement. Fails if the target string isn't found or isn't unique (unless `replace_all=True`).
- **write**: Creates parent directories automatically.

### Bash (`bash_tool.py`)

Runs shell commands via `subprocess.run()`. Output is capped at 30,000 chars (~8k tokens) to prevent context blowout. Default timeout is 30 seconds.

### Web Fetch (`web_fetch.py`)

Fetches a URL with `httpx`, then extracts readable text via `trafilatura`. Output capped at 20,000 chars. Timeout is 30 seconds.

### Load Skill (`skill_tool.py`)

Thin wrapper around `src.skills.load_skill()` — reads `skills/{name}/SKILL.md` and returns its full content.

### Delegate (`delegate.py`)

Spawns a sub-agent (`Agent` instance) with a clean context window and a restricted tool set (no `delegate`). Supports multimodal input — `image_paths` are base64-encoded and sent as content blocks. Sub-agent timeout is 300 seconds.

### Schedule (`schedule_tool.py`)

CRUD operations on `context/schedules.json`. Supports `list`, `add`, `update`, `remove` actions. Cron expressions are validated via `croniter`. File writes are atomic (temp file + rename).

### Attach (`attach.py`)

Records file metadata (path, MIME type, size) into the `attachments` list that the agent carries through a request. The channel layer later delivers these files alongside the text response.

### To Audio (`to_audio.py`) — opt-in

Rewrites text into a spoken-word script via the configured LLM, then calls OpenAI's TTS API (`tts-1` by default) to synthesize an MP3 written to `{config.attachment_dir}/audio/`. Registers the file on the response's `attachments` list so the email and portal channels deliver it alongside the text reply. Voice and model are tunable via the `TTS_VOICE` / `TTS_MODEL` env vars or per-call args. Requires `OPENAI_API_KEY`. Wire it into a skill by adding `tools: to_audio` to the SKILL.md frontmatter.

## Adding a New Tool

1. **Define the schema** in `schemas.py` — add an OpenAI-format function dict to `_SCHEMAS` (or `_OPT_IN_SCHEMAS` for opt-in tools).
2. **Write the executor** in a new or existing file. Signature: `def exec_foo(args: dict, config: AgentConfig) -> str`.
3. **Register the executor** in `dispatcher.py` — add it to `_SYNC_EXECUTORS` (or use `_get_native_async_executor()` for async executors).
4. **Add tests** in `tests/test_tools.py`.
