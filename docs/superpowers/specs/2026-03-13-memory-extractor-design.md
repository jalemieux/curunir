# Memory Extractor — Design Spec

## Overview

Post-conversation memory extraction that runs when a session ends, not per-message. A single LLM call reads the conversation history, follows the `extract-learnings` skill instructions, and writes durable facts to the existing memory structure.

**Deviation from parent spec:** The parent design (`valar-design.md`, lines 89-97) specifies per-turn extraction. This spec changes it to post-session extraction to reduce cost and latency — durable facts don't change meaning when extracted in batch vs. incrementally.

## Trigger Points

| Event | Trigger |
|-------|---------|
| CLI quit | `EOFError` in `_input_loop` → enqueue `command="extract"` |
| `/clear` | Extract before popping the session |
| Timer | Periodic task (configurable, default 1 hour) for sessions that have grown since last extraction |

The timer handles channels without natural endings (Slack, email) and covers abrupt exits (e.g., `KeyboardInterrupt` kills the process before extraction can run — the next timer cycle catches it if the process restarts).

### Timer Deduplication

Track `last_extracted_len: dict[str, int]` in the agent worker — maps session_id to the history length at last extraction. The timer only extracts sessions where `len(history) > last_extracted_len[session_id]`. After extraction, update the watermark.

## Extraction Flow

```
Session ends / timer fires
    │
    ▼
extract_learnings(config, history)
    │
    ├─ Skip if < 2 user messages (trivial/greetings)
    │
    ├─ Read skills/extract-learnings/SKILL.md
    ├─ Read context/memory/README.md
    │
    ▼
Single LLM call
    Input:  conversation history + skill instructions + memory taxonomy
    Output: JSON with file operations + conversation summary
    │
    ▼
Execute file writes (restricted to context/memory/)
    ├─ Append/update category files in context/memory/
    └─ Write summary to context/memory/archives/conversations/YYYY-MM-DD-topic.md
```

## memory_extractor.py

One async function: `extract_learnings(config: AgentConfig, history: list[dict]) -> None`

- Counts user messages in history; returns early if < 2
- Reads the extract-learnings skill and memory README from disk
- Builds an extraction prompt containing:
  - The skill content (extraction method: 6-month test, durable vs ephemeral filter, output format)
  - The memory taxonomy from README.md (so the LLM knows what categories exist)
  - Instruction to output JSON with file operations and a conversation summary
- Calls `call_llm(config.model, messages, tools=[])` — no tools, just text/JSON output
- Parses the JSON response
- **Path safety:** all `file` values in the response are resolved relative to `config.context_dir / "memory"`. Any path that escapes this directory (via `..` or absolute path) is rejected and logged.
- For each file operation: reads the target file (if it exists), appends or writes content
- Writes conversation summary to `archives/conversations/YYYY-MM-DD-{topic_slug}.md`
- Logs errors but never raises — extraction failure must not crash the agent

## LLM Output Format

```json
{
  "facts": [
    {
      "file": "preferences.md",
      "content": "## Topic\n**Source:** cli - 2026-03-13\n**Fact:** concise statement\n**Context:** why this matters"
    }
  ],
  "summary": {
    "topic_slug": "memory-extractor-design",
    "content": "Discussed and designed the memory extraction system..."
  }
}
```

The `file` field is relative to `context/memory/`. Subdirectories are allowed (e.g., `people/james.md`).

## Config Changes

Add `context_dir` to `AgentConfig`:

```python
@dataclass
class AgentConfig:
    model: str = "anthropic/claude-sonnet-4-20250514"
    max_iterations: int = 15
    identity_file: Path = Path("./context/identity.md")
    context_dir: Path = Path("./context")
    skills_dir: Path = Path("./skills")
```

The extractor derives paths from `config.context_dir`:
- Memory dir: `config.context_dir / "memory"`
- Skill file: `config.skills_dir / "extract-learnings" / "SKILL.md"`

## Integration Points

### run.py — agent_worker

`/clear` handler:
```python
if msg.command == "clear":
    history = agent.sessions.pop(msg.session_id, None)
    if history:
        asyncio.create_task(extract_learnings(agent.config, list(history)))
    continue
```

`extract` command handler:
```python
if msg.command == "extract":
    history = agent.sessions.get(msg.session_id)
    if history:
        asyncio.create_task(extract_learnings(agent.config, list(history)))
    continue
```

### run.py — main()

Add a periodic extraction task to the TaskGroup. It iterates `agent.sessions`, compares lengths against `last_extracted_len`, and spawns extraction tasks for sessions that have grown.

### src/channels/cli.py

On `EOFError` in `_input_loop`, enqueue a `command="extract"` message before breaking:
```python
except EOFError:
    msg = IncomingMessage(content="", channel="cli", session_id=SESSION_ID, reply_address={}, command="extract")
    await self.in_queue.put(msg)
    break
```

### skills/extract-learnings/SKILL.md

- **Remove:** the hardcoded Keep/Discard category table (lines 11-19)
- **Add:** instruction to read `context/memory/README.md` for the current taxonomy
- **Preserve:** Quick Filter (6-month test), Output Format, Example sections — these are the behavioral guidance the extraction LLM call relies on

## What Doesn't Change

- `agent.handle()` — untouched
- `call_llm` — reused as-is (no tools for extraction)
- Memory directory structure — already exists
- Agent loop — no per-message extraction overhead

## Error Handling

- All extraction errors are logged and swallowed
- If the LLM returns unparseable JSON, log and skip
- If a file write fails, log and continue with remaining operations
- Extraction runs as a fire-and-forget task — never blocks the main loop
- Path traversal attempts are logged and rejected
