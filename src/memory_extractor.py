# src/memory_extractor.py
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import AgentConfig
from .llm import call_llm
from .memory_indexer import update_indexes
from .skills import load_skill

log = logging.getLogger(__name__)

EXTRACTION_PROMPT = """\
You are a memory extraction system. Your job is to read a conversation and extract durable facts worth remembering long-term.

## Extraction Method

{skill_content}

## Memory Taxonomy

The memory system is organized as follows:

{memory_taxonomy}

## Existing Topics in Memory

The following H2 headings already exist in memory files. If a fact updates an existing topic, reuse its **exact** heading verbatim — the writer replaces the section in place. Only invent a new heading for a genuinely new topic.

{existing_topics}

## Instructions

1. Read the conversation history below.
2. Apply the Quick Filter: "Will this still be true in 6 months?" — extract only durable facts.
3. For each fact, decide which file it belongs in (relative to the memory directory).
4. If the fact updates an existing topic listed above, reuse its exact heading.
5. Also write a brief conversation summary.

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
    *,
    archive_path: Path | None = None,
) -> Path | None:
    """Extract durable learnings from a conversation history and write to memory.

    When `archive_path` is provided, the conversation summary overwrites that
    file instead of allocating a new path from the LLM-chosen slug. Returns the
    path of the summary file written, or None if no summary was produced.
    """
    try:
        return await _extract(config, history, archive_path=archive_path)
    except Exception:
        log.exception("memory extraction failed")
        return None


async def _extract(
    config: AgentConfig,
    history: list[dict],
    *,
    archive_path: Path | None,
) -> Path | None:
    user_count = sum(1 for m in history if m.get("role") == "user")
    if user_count < 2:
        log.debug("skipping extraction: fewer than 2 user messages")
        return None

    memory_dir = config.context_dir / "memory"
    taxonomy_path = memory_dir / "README.md"

    skill_content = load_skill("extract-learnings", config.skill_dirs)
    if skill_content.startswith("Skill not found"):
        skill_content = ""
    memory_taxonomy = taxonomy_path.read_text() if taxonomy_path.exists() else ""

    existing_topics = _format_existing_topics(_collect_existing_headings(memory_dir))

    prompt = EXTRACTION_PROMPT.format(
        skill_content=skill_content,
        memory_taxonomy=memory_taxonomy,
        existing_topics=existing_topics,
    )

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": _format_history(history)},
    ]

    response = await call_llm(config.model, messages, tools=[], api_base=config.api_base, openrouter_provider=config.openrouter_provider)

    if not response.text:
        log.warning("extraction returned empty response")
        return None

    data = _parse_json(response.text)
    if data is None:
        return None

    # Write facts and track which entities they touched
    touched_files: list[str] = []
    for fact in data.get("facts", []):
        if _write_fact(memory_dir, fact) is not None:
            file_rel = fact.get("file")
            if file_rel:
                touched_files.append(file_rel)

    # Write conversation summary, then update progressive-discovery indexes
    summary = data.get("summary")
    if not summary:
        return None

    archive = _write_summary(memory_dir, summary, archive_path=archive_path)
    if archive is not None:
        try:
            update_indexes(
                memory_dir=memory_dir,
                archive_path=archive,
                touched_files=touched_files,
                slug=summary.get("topic_slug", "misc"),
            )
        except Exception:
            log.exception("memory index update failed")
    return archive


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
    resolved = (memory_dir / file_path).resolve()
    memory_resolved = memory_dir.resolve()
    if not str(resolved).startswith(str(memory_resolved) + "/") and resolved != memory_resolved:
        log.warning("path traversal rejected: %s", file_path)
        return None
    return resolved


def _parse_first_heading(content: str) -> str | None:
    """Return the text of the first H2 heading (without `## `) in content, or None."""
    for line in content.splitlines():
        if line.startswith("## ") and not line.startswith("### "):
            return line[3:].strip()
    return None


def _replace_section(text: str, heading: str, new_block: str) -> str | None:
    """Replace the section beginning with `## {heading}` (up to next `## ` or EOF).

    Returns the modified text, or None if the heading was not found.
    """
    lines = text.splitlines(keepends=True)
    marker = f"## {heading}"
    start_idx: int | None = None
    for i, line in enumerate(lines):
        if line.rstrip("\r\n") == marker:
            start_idx = i
            break
    if start_idx is None:
        return None

    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        if lines[j].startswith("## ") and not lines[j].startswith("### "):
            end_idx = j
            break

    block = new_block.rstrip("\n")
    if end_idx < len(lines):
        # Followed by another section — separate with a blank line
        block += "\n\n"
    else:
        block += "\n"

    return "".join(lines[:start_idx]) + block + "".join(lines[end_idx:])


def _collect_existing_headings(memory_dir) -> dict[str, list[str]]:
    """Map each tracked memory file's relative path to its list of H2 headings.

    Excludes `archives/`, `README.md`, and `MEMORY.md`.
    """
    memory_dir = Path(memory_dir)
    if not memory_dir.exists():
        return {}

    result: dict[str, list[str]] = {}
    for path in sorted(memory_dir.rglob("*.md")):
        rel = path.relative_to(memory_dir)
        if rel.parts and rel.parts[0] == "archives":
            continue
        if rel.name in ("README.md", "MEMORY.md"):
            continue
        try:
            text = path.read_text()
        except OSError:
            continue
        headings = [
            line[3:].strip()
            for line in text.splitlines()
            if line.startswith("## ") and not line.startswith("### ")
        ]
        if headings:
            result[rel.as_posix()] = headings
    return result


def _format_existing_topics(headings_by_file: dict[str, list[str]]) -> str:
    if not headings_by_file:
        return "(no existing memory files)"
    lines = []
    for rel_path, headings in headings_by_file.items():
        lines.append(f"- `{rel_path}`:")
        for h in headings:
            lines.append(f"  - {h}")
    return "\n".join(lines)


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

        heading = _parse_first_heading(content)

        if target.exists():
            existing = target.read_text()
            if heading is not None:
                replaced = _replace_section(existing, heading, content)
                if replaced is not None:
                    target.write_text(replaced)
                    return target
            # Heading missing or not present in file — append.
            if not existing.endswith("\n"):
                existing += "\n"
            target.write_text(existing + "\n" + content + ("\n" if not content.endswith("\n") else ""))
        else:
            target.write_text(content + ("\n" if not content.endswith("\n") else ""))
        return target
    except Exception:
        log.exception("failed to write fact to %s", fact.get("file"))
        return None


def _write_summary(
    memory_dir,
    summary: dict,
    *,
    archive_path: Path | None = None,
) -> "Path | None":
    try:
        slug = summary.get("topic_slug", "misc")
        content = summary.get("content", "")
        if not content:
            return None

        if archive_path is not None:
            target = archive_path
        else:
            date_str = datetime.now().astimezone().strftime("%Y-%m-%d")
            filename = f"{date_str}-{slug}.md"
            target = _safe_path(memory_dir, f"archives/conversations/{filename}")
            if target is None:
                return None

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {slug}\n\n{content}\n")
        return target
    except Exception:
        log.exception("failed to write conversation summary")
        return None
