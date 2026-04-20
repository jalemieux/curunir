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
                "description": "Read a file. Supports text files (with line numbers), PDF, DOCX, XLSX, and CSV.",
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
                "description": "Execute a shell command and return stdout + stderr.",
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
                    "Fetch a URL and return the extracted text content (no HTML). "
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
                "name": "run_skill",
                "description": (
                    "Run a skill in a fresh sub-agent context. The sub-agent sees "
                    "only the skill's system prompt, its declared tools, and your "
                    "task+intent. Prefer this over direct tools for anything "
                    "procedural, recurring, or likely to produce large output."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill": {
                            "type": "string",
                            "description": "Name of the skill to run (must match the skill manifest).",
                        },
                        "task": {
                            "type": "string",
                            "description": (
                                "The concrete action the sub-agent should perform. "
                                "E.g. 'research the state of small-LLM inference on Mac', "
                                "'send a reply to the email in thread X', "
                                "'submit a fix PR for issue #42'."
                            ),
                        },
                        "intent": {
                            "type": "string",
                            "description": (
                                "What you need back — the user's goal, not a restatement "
                                "of the task. E.g. 'a written report', 'confirmation the "
                                "email was sent', 'the PR URL'. The sub-agent returns "
                                "exactly this; if you omit the user's goal, it is lost."
                            ),
                        },
                    },
                    "required": ["skill", "task", "intent"],
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
                            "enum": ["list", "add", "update", "remove"],
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
]

for _s in _SCHEMAS:
    _register(_s)


# Opt-in tools — not included by default, loaded when a skill requires them.
_OPT_IN_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "attach",
            "description": (
                "Attach a file to your response. The file will be delivered "
                "to the user alongside your text reply. Use this when you've "
                "created a report, document, or other artifact the user should "
                "receive as a file rather than inline text."
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

for _s in _OPT_IN_SCHEMAS:
    _register(_s, opt_in=True)
