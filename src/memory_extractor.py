# src/memory_extractor.py
import json
import logging
from datetime import datetime, timezone

from .config import AgentConfig
from .llm import call_llm

log = logging.getLogger(__name__)

EXTRACTION_PROMPT = """\
You are a memory extraction system. Your job is to read a conversation and extract durable facts worth remembering long-term.

## Extraction Method

{skill_content}

## Memory Taxonomy

The memory system is organized as follows:

{memory_taxonomy}

## Instructions

1. Read the conversation history below.
2. Apply the Quick Filter: "Will this still be true in 6 months?" — extract only durable facts.
3. For each fact, decide which file it belongs in (relative to the memory directory).
4. Also write a brief conversation summary.

Respond with ONLY valid JSON in this exact format:

```json
{{
  "facts": [
    {{
      "file": "preferences.md",
      "content": "## Topic\\n**Source:** channel - date\\n**Fact:** concise statement\\n**Context:** why this matters"
    }}
  ],
  "summary": {{
    "topic_slug": "short-hyphenated-topic",
    "content": "Brief summary of the conversation..."
  }}
}}
```

If there is nothing worth extracting, return: {{"facts": [], "summary": {{"topic_slug": "misc", "content": "Brief summary..."}}}}

The `file` field is relative to the memory directory. Subdirectories are allowed (e.g., `people/james.md`).
"""


async def extract_learnings(
    config: AgentConfig,
    history: list[dict],
) -> None:
    """Extract durable learnings from a conversation history and write to memory."""
    try:
        await _extract(config, history)
    except Exception:
        log.exception("memory extraction failed")


async def _extract(
    config: AgentConfig,
    history: list[dict],
) -> None:
    user_count = sum(1 for m in history if m.get("role") == "user")
    if user_count < 2:
        log.debug("skipping extraction: fewer than 2 user messages")
        return

    memory_dir = config.context_dir / "memory"
    skill_path = config.skills_dir / "extract-learnings" / "SKILL.md"
    taxonomy_path = memory_dir / "README.md"

    skill_content = skill_path.read_text() if skill_path.exists() else ""
    memory_taxonomy = taxonomy_path.read_text() if taxonomy_path.exists() else ""

    prompt = EXTRACTION_PROMPT.format(
        skill_content=skill_content,
        memory_taxonomy=memory_taxonomy,
    )

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": _format_history(history)},
    ]

    response = await call_llm(
        config.model, messages, tools=[],
        max_tokens=config.max_tokens,
        api_base=config.api_base,
        openrouter_provider=config.openrouter_provider,
    )

    if not response.text:
        log.warning("extraction returned empty response")
        return

    data = _parse_json(response.text)
    if data is None:
        return

    # Write facts
    for fact in data.get("facts", []):
        _write_fact(memory_dir, fact)

    # Write conversation summary
    summary = data.get("summary")
    if summary:
        _write_summary(memory_dir, summary)


def _format_history(history: list[dict]) -> str:
    lines = []
    for msg in history:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        lines.append(f"[{role}]: {content}")
    return "\n\n".join(lines)


def _parse_json(text: str) -> dict | None:
    # Try to extract JSON from markdown code blocks or raw text
    cleaned = text.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                try:
                    return json.loads(part)
                except json.JSONDecodeError:
                    continue
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        log.warning("failed to parse extraction response as JSON")
        return None


def _safe_path(memory_dir, file_path: str) -> "Path | None":
    """Resolve file path relative to memory_dir, rejecting escapes."""
    from pathlib import Path

    resolved = (memory_dir / file_path).resolve()
    memory_resolved = memory_dir.resolve()
    if not str(resolved).startswith(str(memory_resolved) + "/") and resolved != memory_resolved:
        log.warning("path traversal rejected: %s", file_path)
        return None
    return resolved


def _write_fact(memory_dir, fact: dict) -> "Path | None":
    try:
        file_rel = fact.get("file", "")
        content = fact.get("content", "")
        if not file_rel or not content:
            return None

        target = _safe_path(memory_dir, file_rel)
        if target is None:
            return None

        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists():
            existing = target.read_text()
            if not existing.endswith("\n"):
                existing += "\n"
            target.write_text(existing + "\n" + content + "\n")
        else:
            target.write_text(content + "\n")
        return target
    except Exception:
        log.exception("failed to write fact to %s", fact.get("file"))
        return None


def _write_summary(memory_dir, summary: dict) -> "Path | None":
    try:
        slug = summary.get("topic_slug", "misc")
        content = summary.get("content", "")
        if not content:
            return None

        date_str = datetime.now().astimezone().strftime("%Y-%m-%d")
        archive_dir = memory_dir / "archives" / "conversations"
        archive_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{date_str}-{slug}.md"
        target = _safe_path(memory_dir, f"archives/conversations/{filename}")
        if target is None:
            return None

        target.write_text(f"# {slug}\n\n{content}\n")
        return target
    except Exception:
        log.exception("failed to write conversation summary")
        return None
