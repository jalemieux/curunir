# src/memory_extractor.py
import json
import logging
import os
import tempfile
from datetime import datetime
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

## Instructions

1. Read the conversation history below.
2. Apply the Quick Filter: "Will this still be true in 6 months?" — extract only durable facts.
3. For each fact, decide which file it belongs in (relative to the memory directory).
4. Give each fact a descriptive H2 heading naming its topic or entity. Don't worry about matching headings already in memory — a later consolidation pass merges and deduplicates entries.
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

CONSOLIDATION_PROMPT = """\
You are a memory consolidation system. You maintain a single memory file by merging
newly extracted facts into its current content.

## Target file

`{file_rel}`

## Current file content

{current_content}

## Newly extracted facts to merge in

{new_facts}

## Instructions

Produce one merged version of the file:

1. Merge entries about the same topic or entity into a single coherent entry. When
   a new fact updates or supersedes an existing one, keep the current information.
2. Preserve every distinct fact. Only collapse entries that are genuine duplicates
   or restatements — never drop information just because it overlaps.
3. Drop entries the conversation clearly shows are resolved, completed, or no longer
   true. Be conservative: if there is no clear signal that an entry is stale, keep it.
4. Preserve hand-curated structure — for example a catalog or index near the top of
   the file, section ordering, and any non-fact prose.
5. Keep the existing formatting conventions (H2 headings, `**Source:**` / `**Fact:**`
   / `**Context:**` lines).

Respond with ONLY the full merged file content as Markdown. Do not wrap it in a code
fence. Do not add any commentary before or after.
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

    prompt = EXTRACTION_PROMPT.format(
        skill_content=skill_content,
        memory_taxonomy=memory_taxonomy,
    )

    conversation = _format_history(history)
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": conversation},
    ]

    response = await call_llm(config.model, messages, tools=[], api_base=config.api_base, openrouter_provider=config.openrouter_provider)

    if not response.text:
        log.warning("extraction returned empty response")
        return None

    data = _parse_json(response.text)
    if data is None:
        return None

    # Group facts by target file, then run one consolidation pass per touched
    # file: hand the LLM the current file plus the new facts and write back the
    # merged, deduplicated result.
    facts_by_file: dict[str, list[dict]] = {}
    for fact in data.get("facts", []):
        file_rel = fact.get("file")
        content = fact.get("content")
        if file_rel and content:
            facts_by_file.setdefault(file_rel, []).append(fact)

    touched_files: list[str] = []
    for file_rel, file_facts in facts_by_file.items():
        result = await _consolidate_file(
            config, memory_dir, file_rel, file_facts, conversation
        )
        if result is not None:
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


def _strip_code_fence(text: str) -> str:
    """Strip a single wrapping ``` code fence, if the LLM added one anyway."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def _atomic_write(target: Path, content: str) -> None:
    """Write `content` to `target` atomically via a temp file + os.replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, target)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _snapshot_file(memory_dir, file_rel: str, content: str) -> None:
    """Snapshot prior file content to archives/memory-snapshots/ before a rewrite.

    One snapshot per file per day — re-running within a day overwrites it.
    """
    date_str = datetime.now().astimezone().strftime("%Y-%m-%d")
    flattened = file_rel.replace("/", "-")
    snapshot = _safe_path(
        memory_dir, f"archives/memory-snapshots/{date_str}-{flattened}"
    )
    if snapshot is None:
        return
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(content)


def _append_facts(target: Path, current: str, facts_text: str) -> Path:
    """Fallback: append raw facts so nothing is lost when consolidation fails."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if current:
        if not current.endswith("\n"):
            current += "\n"
        merged = current + "\n" + facts_text
    else:
        merged = facts_text
    if not merged.endswith("\n"):
        merged += "\n"
    target.write_text(merged)
    return target


async def _consolidate_file(
    config: AgentConfig,
    memory_dir,
    file_rel: str,
    new_facts: list[dict],
    conversation: str,
) -> "Path | None":
    """Merge `new_facts` into the memory file at `file_rel` via an LLM pass.

    Reads the current file, asks the LLM to produce one merged/deduped/pruned
    version, snapshots the prior content, and writes the result atomically. On
    any LLM or parse failure, falls back to appending the raw facts so nothing
    is lost.
    """
    target = _safe_path(memory_dir, file_rel)
    if target is None:
        return None

    facts_text = "\n\n".join(
        f.get("content", "").strip() for f in new_facts if f.get("content", "").strip()
    )
    if not facts_text:
        return None

    current = target.read_text() if target.exists() else ""

    try:
        prompt = CONSOLIDATION_PROMPT.format(
            file_rel=file_rel,
            current_content=current or "(this file does not exist yet — create it)",
            new_facts=facts_text,
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": conversation},
        ]
        response = await call_llm(
            config.model,
            messages,
            tools=[],
            api_base=config.api_base,
            openrouter_provider=config.openrouter_provider,
        )
        merged = _strip_code_fence(response.text) if response.text else ""
        if not merged.strip():
            raise ValueError("empty consolidation response")
    except Exception:
        log.exception(
            "consolidation failed for %s — falling back to append", file_rel
        )
        return _append_facts(target, current, facts_text)

    if current:
        _snapshot_file(memory_dir, file_rel, current)

    if not merged.endswith("\n"):
        merged += "\n"
    _atomic_write(target, merged)
    return target


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
