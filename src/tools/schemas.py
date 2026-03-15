ALL_TOOL_SCHEMAS: dict[str, dict] = {}


def _register(schema: dict) -> dict:
    """Register a schema in the global dict and return it."""
    ALL_TOOL_SCHEMAS[schema["function"]["name"]] = schema
    return schema


def get_tool_schemas(names: list[str] | None = None) -> list[dict]:
    """Return tool schemas. If names is provided, return only those tools."""
    if names is not None:
        return [ALL_TOOL_SCHEMAS[n] for n in names if n in ALL_TOOL_SCHEMAS]
    return list(ALL_TOOL_SCHEMAS.values())


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
]

for _s in _SCHEMAS:
    _register(_s)
