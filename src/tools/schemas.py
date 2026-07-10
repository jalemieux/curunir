ALL_TOOL_SCHEMAS: dict[str, dict] = {}
_DEFAULT_TOOL_NAMES: list[str] = []
_OPT_IN_TOOL_NAMES: list[str] = []


def _register(schema: dict, *, opt_in: bool = False) -> dict:
    """Register a schema in the global dict and return it."""
    name = schema["function"]["name"]
    ALL_TOOL_SCHEMAS[name] = schema
    if opt_in:
        _OPT_IN_TOOL_NAMES.append(name)
    else:
        _DEFAULT_TOOL_NAMES.append(name)
    return schema


def get_tool_schemas(names: list[str] | None = None) -> list[dict]:
    """Return tool schemas.

    If names is provided, return only those tools (from both default and opt-in).
    Otherwise, return only default tools (excludes opt-in tools).
    """
    if names is not None:
        return [ALL_TOOL_SCHEMAS[n] for n in names if n in ALL_TOOL_SCHEMAS]
    return [ALL_TOOL_SCHEMAS[n] for n in _DEFAULT_TOOL_NAMES]


# Register all tool schemas at import time
_SCHEMAS = [
        {
            "type": "function",
            "function": {
                "name": "glob",
                "description": "Find files matching a glob pattern.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Glob pattern to match files against.",
                        },
                        "path": {
                            "type": "string",
                            "description": "Directory to search in. Defaults to current working directory.",
                        },
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "grep",
                "description": "Search file contents using ripgrep.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Regular expression pattern to search for.",
                        },
                        "path": {
                            "type": "string",
                            "description": "File or directory to search in.",
                        },
                        "glob": {
                            "type": "string",
                            "description": "Glob pattern to filter files.",
                        },
                        "output_mode": {
                            "type": "string",
                            "enum": ["content", "files_with_matches", "count"],
                            "description": "Output mode for results.",
                        },
                        "context": {
                            "type": "integer",
                            "description": "Number of lines of context to show around each match.",
                        },
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": (
                    "Read a file. Supports text files, PDF, DOCX, XLSX, and "
                    "CSV; output is line-numbered. Reading a large file "
                    "without `limit` returns its document card or a head "
                    "preview instead of the full body — use offset/limit for "
                    "a specific range, grep to locate content, or the "
                    "document-ingest skill to card a big document for "
                    "navigation."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to the file to read.",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "1-based line number to start reading from.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Number of lines to read.",
                        },
                    },
                    "required": ["file_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit",
                "description": "Replace an exact string in a file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to the file to edit.",
                        },
                        "old_string": {
                            "type": "string",
                            "description": "The exact text to replace.",
                        },
                        "new_string": {
                            "type": "string",
                            "description": "The text to replace it with.",
                        },
                        "replace_all": {
                            "type": "boolean",
                            "description": "Replace all occurrences instead of requiring uniqueness.",
                        },
                    },
                    "required": ["file_path", "old_string", "new_string"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write",
                "description": "Write content to a file, creating parent directories if needed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to the file to write.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Content to write to the file.",
                        },
                    },
                    "required": ["file_path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": (
                    "Execute a shell command and return stdout + stderr. "
                    "Commands already run from the repo root, so invoke skill "
                    "CLIs with repo-relative paths (e.g. "
                    "`python skills/<skill>/<cli>.py ...`) without `cd`-ing "
                    "elsewhere. Do not suppress stderr (`2>/dev/null`) on "
                    "verification commands — a failed command must surface, "
                    "not pass silently."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The shell command to execute.",
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Timeout in seconds. Defaults to 30.",
                        },
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "load_skill",
                "description": "Load a skill's instructions by name.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name of the skill to load.",
                        },
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": (
                    "Fetch a URL and return the extracted text content. "
                    "Extracts readable text from HTML pages and PDFs. "
                    "Other binary formats (DOCX, ZIP, images) are not supported. "
                    "Use this instead of curl for reading web pages — it strips "
                    "navigation, scripts, and ads, returning only readable text."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL to fetch.",
                        },
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delegate",
                "description": (
                    "Delegate a task to a sub-agent with a clean context window. "
                    "Use this for tasks that involve processing large documents, "
                    "analyzing images, or doing multi-step research. The sub-agent "
                    "has access to all tools (read, write, bash, etc.) but runs in "
                    "isolation — its intermediate work won't fill up your context. "
                    "You get back only the final answer."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "Clear description of what the sub-agent should do.",
                        },
                        "image_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of image file paths to include for visual analysis.",
                        },
                    },
                    "required": ["task"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "schedule",
                "description": (
                    "Manage scheduled tasks that run autonomously on a cron schedule. "
                    "Use this to set up recurring tasks like morning briefs, PR checks, "
                    "or maintenance jobs. Scheduled tasks run in their own session with "
                    "no conversation context, so make prompts self-contained."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["list", "add", "update", "remove", "toggle"],
                            "description": "The operation to perform.",
                        },
                        "id": {
                            "type": "string",
                            "description": "Human-readable task ID (e.g. 'morning-brief'). Required for add/update/remove.",
                        },
                        "cron": {
                            "type": "string",
                            "description": "5-field cron expression (e.g. '0 9 * * *' for 9am daily). Required for add.",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "The instruction to execute when the task fires. Must be self-contained. Required for add.",
                        },
                        "skill": {
                            "type": "string",
                            "description": "Optional skill name to load before executing the prompt.",
                        },
                        "enabled": {
                            "type": "boolean",
                            "description": "Enable or disable the task. Used with update.",
                        },
                    },
                    "required": ["action"],
                },
            },
        },
        {
        "type": "function",
        "function": {
            "name": "attach",
            "description": (
                "Send a file to the user as an attachment on this response. "
                "Use this when the user asks for a file or when you've produced "
                "an artifact (report, document, etc.). Pass an absolute path; "
                "do not read, glob, or stat the file beforehand if the user "
                "already gave you the path. Call this once per file — repeated "
                "calls duplicate the attachment. The attachment alone is a "
                "complete reply; no follow-up text is required."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file to attach.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Display name for the attachment (e.g. 'research-report.md').",
                    },
                },
                "required": ["path"],
            },
        },
    },
]

for _s in _SCHEMAS:
    _register(_s)


# Opt-in tools — not included by default, loaded when a skill requires them.
_OPT_IN_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "portfolio",
            "description": (
                "Read and update the owner's balance sheet (assets, "
                "liabilities, net worth). The engine does all math and writes "
                "— never compute a total yourself. Reads: networth, rollup, "
                "list, show, re_equity, pnl, query (read-only SQL), render. "
                "Writes: add, add_liability, set, rm, import_rows (bulk CSV "
                "load with an account-total self-check), refresh (re-price "
                "market holdings). Pass the operation in `action` and its "
                "parameters in `args`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["networth", "rollup", "list", "show", "re_equity",
                                 "pnl", "query", "render", "add", "add_liability",
                                 "set", "rm", "import_rows", "refresh"],
                        "description": "The operation to run.",
                    },
                    "args": {
                        "type": "object",
                        "description": (
                            "Operation parameters, e.g. {class,label,value,...} "
                            "for add; {id,fields:{...}} for set; "
                            "{rows:[...],account,stated_total} for import_rows; "
                            "{sql} for query."
                        ),
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crm",
            "description": (
                "Read and update the marketing CRM (leads + pipeline). The "
                "engine does all writes and counts — never track pipeline in "
                "prose. Reads: list, show, pipeline (counts by stage), "
                "activity (interaction ledger), query (read-only SQL), render. "
                "Writes: add (a lead), set (update fields), set_stage (advance "
                "a lead + log a stage_change), rm, log (an interaction), "
                "import_rows (bulk lead load). Stages: new, contacted, "
                "qualified, trial, won, lost. Pass the operation in `action` "
                "and its parameters in `args`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "show", "pipeline", "activity",
                                 "query", "render", "add", "set", "set_stage",
                                 "rm", "log", "import_rows"],
                        "description": "The operation to run.",
                    },
                    "args": {
                        "type": "object",
                        "description": (
                            "Operation parameters, e.g. "
                            "{name,email,company,source,stage,owner} for add; "
                            "{id,fields:{...}} for set; {id,stage} for "
                            "set_stage; {lead_id,kind,body} for log; "
                            "{rows:[...],source,owner} for import_rows; "
                            "{sql} for query."
                        ),
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "to_audio",
            "description": (
                "Rewrite text for natural speech and synthesize it into an "
                "MP3 audio attachment on this response. Use when the user "
                "wants to listen to a digest, summary, or article. Pass the "
                "raw text in `content`; the tool handles the spoken-word "
                "rewrite (bullet → prose, emoji handling, transitions) "
                "internally before calling TTS. Optional `voice`, `model`, "
                "and `filename` arguments override the defaults."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Text to convert to spoken audio.",
                    },
                    "voice": {
                        "type": "string",
                        "description": "OpenAI TTS voice (alloy, echo, fable, onyx, nova, shimmer). Defaults to TTS_VOICE.",
                    },
                    "model": {
                        "type": "string",
                        "description": "OpenAI TTS model (tts-1 or tts-1-hd). Defaults to TTS_MODEL.",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Output filename for the MP3. Defaults to digest-YYYY-MM-DD.mp3.",
                    },
                },
                "required": ["content"],
            },
        },
    },
]

for _s in _OPT_IN_SCHEMAS:
    _register(_s, opt_in=True)
