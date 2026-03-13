# Core Agentic Loop — Design Spec

## Overview

The core agentic loop for Curunir: the central engine that receives a message, builds a system prompt, calls an LLM in a tool-calling loop, and returns a text response. This covers the agent loop, LLM wrapper, system prompt builder, tool dispatcher with all 7 tool executors, skill scanner, and a test script.

Out of scope: channels, queues, memory extraction, config file loading, CLI output formatting.

## File Structure

```
curunir/
├── run.py                          # Test script (async REPL)
├── requirements.txt
├── src/
│   ├── config.py                   # AgentConfig dataclass
│   ├── llm.py                      # LiteLLM wrapper
│   ├── skills.py                   # Skill scanner + manifest builder
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── agent.py                # Agent class, session management, loop
│   │   └── system_prompt.py        # Build prompt from identity + skills + timestamp
│   └── tools/
│       ├── __init__.py
│       ├── dispatcher.py           # Tool registry + execute_tool_call
│       ├── schemas.py              # 7 tool JSON schemas (OpenAI format)
│       ├── fs_tools.py             # Glob, Grep, Read, Edit, Write executors
│       ├── bash_tool.py            # Bash executor
│       └── skill_tool.py           # load_skill executor
├── context/
│   └── identity.md                 # Agent identity (created for testing)
├── skills/                         # Empty, populated per deployment
└── tests/
```

## Components

### AgentConfig (`src/config.py`)

Simple dataclass holding runtime configuration. No file parsing — that's a separate concern added later.

```python
@dataclass
class AgentConfig:
    model: str = "anthropic/claude-sonnet-4-20250514"
    max_iterations: int = 15
    identity_file: Path = Path("./context/identity.md")
    skills_dir: Path = Path("./skills")
```

### Agent Loop (`src/agent/agent.py`)

The `Agent` class owns sessions and runs the agentic loop.

```python
class Agent:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.sessions: dict[str, list[dict]] = {}
        # Identity + skill manifest are static; built once at init.
        # Timestamp is injected per-call in handle().
        self.static_prompt = build_static_prompt(config)

    async def handle(self, message: str, session_id: str) -> str:
        history = self.sessions.setdefault(session_id, [])
        history.append({"role": "user", "content": message})

        system_prompt = self.static_prompt + f"\n\nCurrent time: {datetime.now().isoformat()}"
        messages = [{"role": "system", "content": system_prompt}] + history

        for _ in range(self.config.max_iterations):
            response = await call_llm(self.config.model, messages, get_tool_schemas())

            # LLM can return both text and tool_calls in one response.
            # If tool_calls are present, execute them (text is incidental).
            # Only return text when there are no tool_calls.
            if response.tool_calls:
                assistant_msg = {"role": "assistant", "tool_calls": response.tool_calls}
                if response.text:
                    assistant_msg["content"] = response.text
                history.append(assistant_msg)
                for tool_call in response.tool_calls:
                    result = await asyncio.to_thread(
                        execute_tool_call,
                        tool_call["function"]["name"],
                        json.loads(tool_call["function"]["arguments"]),
                        self.config,
                    )
                    history.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": result,
                    })
                messages = [{"role": "system", "content": system_prompt}] + history
                continue

            if response.text:
                history.append({"role": "assistant", "content": response.text})
                return response.text

            # Neither text nor tool_calls — unexpected. Break to avoid silent looping.
            history.append({"role": "assistant", "content": ""})
            return "Error: LLM returned empty response."

        return "Iteration limit reached."
```

Key behaviors:
- Sessions are in-memory dicts keyed by session_id
- Identity + skill manifest are built once at init; timestamp is refreshed per `handle()` call
- When the LLM returns both text and tool_calls, tool_calls take priority (text is preserved in history but not returned yet)
- Tool executors are synchronous — wrapped in `asyncio.to_thread()` to avoid blocking the event loop (matters when channels are added later)
- Tool results are appended as `role: tool` messages with matching `tool_call_id`
- If the LLM returns neither text nor tool_calls, the loop breaks with an error
- If max_iterations is hit, returns a plain text error

### LLM Wrapper (`src/llm.py`)

Thin async wrapper around LiteLLM. Returns a clean dataclass — no LiteLLM types leak out.

```python
@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[dict] | None  # [{id, function: {name, arguments}}]

async def call_llm(model: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
    response = await litellm.acompletion(
        model=model,
        messages=messages,
        tools=tools,
    )
    choice = response.choices[0].message
    text = choice.content if choice.content else None
    tool_calls = None
    if choice.tool_calls:
        tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in choice.tool_calls
        ]
    return LLMResponse(text=text, tool_calls=tool_calls)
```

No retry logic, no streaming, no rate limiting. Those are separate concerns.

### System Prompt Builder (`src/agent/system_prompt.py`)

Assembles the static portion of the system prompt (identity + skill manifest). The timestamp is appended per-call in `Agent.handle()`.

```python
def build_static_prompt(config: AgentConfig) -> str:
    if not config.identity_file.exists():
        raise FileNotFoundError(
            f"Identity file not found: {config.identity_file}. "
            "Curunir requires an identity file to start."
        )
    identity = config.identity_file.read_text()
    manifest = build_skill_manifest(config.skills_dir)
    parts = [identity]
    if manifest:
        parts.append(manifest)
    return "\n\n".join(parts)
```

The identity file is required — the agent refuses to start without one. The skill manifest is a markdown table built from YAML frontmatter `description` fields (not from the markdown body). If no skills exist, the manifest is omitted.

### Skills Scanner (`src/skills.py`)

Scans `skills/*/SKILL.md`, parses YAML frontmatter, builds a manifest table.

```python
def build_skill_manifest(skills_dir: Path) -> str:
    """Scan skills dir, return markdown table of name + description."""
    skills = []
    for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
        frontmatter = parse_frontmatter(skill_file.read_text())
        skills.append((frontmatter["name"], frontmatter["description"]))

    if not skills:
        return ""

    lines = ["## Available Skills", "| Skill | When to Use |", "|-------|-------------|"]
    for name, desc in skills:
        lines.append(f"| {name} | {desc} |")
    return "\n".join(lines)

def load_skill(name: str, skills_dir: Path) -> str:
    """Load full SKILL.md content by name."""
    path = skills_dir / name / "SKILL.md"
    if not path.exists():
        return f"Skill not found: {name}"
    return path.read_text()

def parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from markdown."""
    if not text.startswith("---"):
        return {}
    _, fm, _ = text.split("---", 2)
    # Simple YAML parsing for name/description
    result = {}
    for line in fm.strip().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip().strip("'\"")
    return result
```

### Tool Dispatcher (`src/tools/dispatcher.py`)

Registry dict + single dispatch function. All tools return strings.

```python
EXECUTORS = {
    "glob": exec_glob,
    "grep": exec_grep,
    "read": exec_read,
    "edit": exec_edit,
    "write": exec_write,
    "bash": exec_bash,
    "load_skill": exec_load_skill,
}

def execute_tool_call(name: str, args: dict, config: AgentConfig) -> str:
    executor = EXECUTORS.get(name.lower())
    if not executor:
        return f"Unknown tool: {name}"
    return executor(args, config)
```

### Tool Executors

All executors have the signature `(args: dict, config: AgentConfig) -> str`. Errors return error strings, never raise exceptions.

**fs_tools.py** — five file system tools:

| Executor | Implementation |
|----------|---------------|
| `exec_glob` | `glob.glob(pattern, root_dir=path, recursive=True)`, returns newline-joined paths |
| `exec_grep` | Shells out to `rg` with args mapped to flags, returns stdout |
| `exec_read` | Opens file, reads lines with offset/limit, returns with line numbers (`cat -n` style) |
| `exec_edit` | Reads file, does `str.replace(old, new, count)`, writes back. Fails if old_string not found or not unique (unless replace_all) |
| `exec_write` | Creates parent dirs if needed, writes content to file |

**bash_tool.py** — one tool:

| Executor | Implementation |
|----------|---------------|
| `exec_bash` | `subprocess.run(command, shell=True, capture_output=True, timeout=timeout)`, returns stdout+stderr |

Default timeout: 30 seconds.

**skill_tool.py** — one tool:

| Executor | Implementation |
|----------|---------------|
| `exec_load_skill` | Delegates to `skills.load_skill(name, config.skills_dir)` |

### Tool Schemas (`src/tools/schemas.py`)

Exports `get_tool_schemas() -> list[dict]` returning all 7 tool definitions in OpenAI function-calling format. Each entry:

```python
{
    "type": "function",
    "function": {
        "name": "tool_name",
        "description": "What it does",
        "parameters": {
            "type": "object",
            "properties": { ... },
            "required": [ ... ],
        },
    },
}
```

### Test Script (`run.py`)

Minimal async REPL at repo root:

```python
import asyncio
from src.config import AgentConfig
from src.agent.agent import Agent

async def main():
    config = AgentConfig()
    agent = Agent(config)
    print("Curunir agent ready. Type 'quit' to exit.")
    while True:
        user_input = input("> ")
        if user_input.strip().lower() == "quit":
            break
        response = await agent.handle(user_input, session_id="cli")
        print(response)

if __name__ == "__main__":
    asyncio.run(main())
```

### Dependencies

```
litellm
pyyaml
python-dotenv
```

`pyyaml` is listed for future config loading but not strictly needed for the core loop (frontmatter parsing uses simple string splitting). `python-dotenv` is included so `run.py` can load API keys from `.env`.

## What This Builds

After implementation, you can:
1. Write a `context/identity.md` with any persona
2. Drop skills into `skills/*/SKILL.md`
3. Set an API key in `.env`
4. Run `python run.py` and have a working agentic assistant that can use all 7 tools

## What Comes Next (not in this spec)

- Channels (CLI with rich output, Slack, Email)
- Message queues (InQueue/OutQueue)
- Memory extraction (post-conversation fact extraction)
- Config file loading (config.yaml with env var interpolation)
- Docker packaging
