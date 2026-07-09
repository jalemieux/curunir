# src/document_ingest.py
"""One-shot document ingestion → document card (see docs/document-ingestion.md).

A card is a compact navigation map (~1k tokens) for a large document: doc
type/period/entities, key figures with line refs, and a section map. The
conversation carries the card; the raw text is consulted later via targeted
`read` calls guided by it.

Ingestion is deliberately a single tool-less LLM call (modeled on
memory_extractor.extract_learnings, NOT on delegate): the full line-numbered
document goes into one prompt and the throwaway context is discarded. That is
cheaper than an agentic reader (the document is sent exactly once), faster
(one round-trip — the UI blocks on it), produces better cards (the model sees
the whole document at once), and is safe to run automatically on untrusted
uploads — with zero tool surface, a malicious document can distort nothing
but the wording of its own card.

Documents beyond MAX_ONESHOT_CHARS fall back to map-reduce: a partial card
per chunk, then one merge call. Line numbering happens *before* splitting so
chunk cards cite absolute, `read`-addressable line numbers.
"""
import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import AgentConfig
from .llm import call_llm
from .skills import load_skill
from .tools.fs_tools import _BINARY_READERS, _IMAGE_EXTENSIONS
from .usage_store import UsageRecord, UsageStore

log = logging.getLogger(__name__)

# ~150k tokens at ~4 chars/token — the one-shot ceiling, leaving headroom for
# instructions + the card in a 200k window. Beyond this, map-reduce.
MAX_ONESHOT_CHARS = 600_000

CARD_SUFFIX = ".card.md"


class DocumentIngestError(Exception):
    """Ingestion failed in a way the caller should surface (bad input, empty LLM reply)."""


INGESTION_PROMPT = """\
You are a document ingestion system. You will be given the full text of a \
document, line-numbered in `N<TAB>line` format. Produce a **document card**: \
a compact navigation map that lets an assistant answer questions about this \
document later through targeted reads of specific line ranges, without \
re-reading the whole document.

## Card format

{card_spec}

## Rules

- Cite every location as a line range taken from the numbering (e.g. \
"lines 210-260"). Never invent line numbers.
- The card routes, it does not analyze: record what is where and the key \
identifying facts/figures with their locations. No conclusions, no advice.
- Keep the card compact — aim for well under 1500 tokens.
- Respond with ONLY the card markdown. No preamble, no code fences.
"""

# Fallback card spec when the document-ingest skill is not on disk (e.g. a
# persona bundle without it). The skill's SKILL.md is the canonical,
# persona-templatable version.
_DEFAULT_CARD_SPEC = """\
# Document card: <filename>

- **Type:** <what kind of document, and its period/date if any>
- **Entities:** <organizations, people, accounts the document is about>

## Key figures
<the handful of facts/figures someone would ask about — each with `lines N-M`>

## Section map
<one line per section: `<section name> — lines N-M`>

## Gist
<2-4 sentence neutral summary of what the document covers>
"""

_MERGE_PROMPT = """\
The document below was too large for one pass, so partial cards were produced \
for consecutive chunks (line numbers are absolute and consistent across \
chunks). Merge them into ONE document card in the same format: unify the \
section maps, keep every key figure with its line reference, and write a \
single gist. Respond with ONLY the merged card markdown.
"""


def card_path(path: Path) -> Path:
    """Sibling card file for a document: <name>.card.md next to it."""
    return path.with_name(path.name + CARD_SUFFIX)


def _extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _IMAGE_EXTENSIONS:
        raise DocumentIngestError(
            f"{path.name} is an image; image files are not ingestible as documents"
        )
    reader = _BINARY_READERS.get(suffix)
    if reader:
        try:
            return reader(path)
        except Exception as exc:
            raise DocumentIngestError(f"could not extract text from {path.name}: {exc}") from exc
    return path.read_bytes().decode("utf-8", errors="replace")


def _number_lines(text: str) -> str:
    return "\n".join(f"{i}\t{line}" for i, line in enumerate(text.splitlines(), 1))


def _split_chunks(numbered: str, max_chars: int) -> list[str]:
    """Split numbered text into chunks of at most max_chars, on line boundaries.

    Numbering happened before the split, so line references stay absolute.
    A single line longer than max_chars becomes its own chunk (never dropped).
    """
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in numbered.splitlines():
        if current and size + len(line) + 1 > max_chars:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def _strip_frontmatter(text: str) -> str:
    """Drop the YAML frontmatter block — it's agent-routing metadata, not card spec."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + len("\n---"):].lstrip("\n")


def _card_spec(config: AgentConfig) -> str:
    content = load_skill("document-ingest", config.skill_dirs)
    if content.startswith("Skill not found"):
        return _DEFAULT_CARD_SPEC
    return _strip_frontmatter(content)


async def ingest_document(
    path: str | Path,
    config: AgentConfig,
    usage_store: UsageStore | None = None,
) -> str:
    """Produce (or reuse) the document card for `path` and return its text.

    Writes the card to `<path>.card.md`. An existing non-empty card
    short-circuits without an LLM call, so ingestion is idempotent per file.
    Usage is recorded under session id `ingest:<sha256[:16]>` so ingestion
    cost is visible in the usage dashboard, separate from conversations.
    """
    path = Path(path)
    if not path.is_file():
        raise DocumentIngestError(f"Document not found: {path}")

    cpath = card_path(path)
    if cpath.is_file():
        cached = cpath.read_text()
        if cached.strip():
            log.info("ingest: reusing existing card %s", cpath)
            return cached

    raw = path.read_bytes()
    session_id = f"ingest:{hashlib.sha256(raw).hexdigest()[:16]}"

    text = _extract_text(path)
    if not text.strip():
        raise DocumentIngestError(f"{path.name} is empty; nothing to ingest")

    numbered = _number_lines(text)
    system = INGESTION_PROMPT.format(card_spec=_card_spec(config))

    async def _call(user_content: str) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        response = await call_llm(
            config.model,
            messages,
            tools=[],
            api_base=config.api_base,
            openrouter_provider=config.openrouter_provider,
        )
        if usage_store is not None:
            record = UsageRecord(
                ts=datetime.now(timezone.utc),
                session_id=session_id,
                model=response.usage.model or config.model,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                cached_prompt_tokens=response.usage.cached_prompt_tokens,
                reasoning_tokens=response.usage.reasoning_tokens,
                image_tokens=response.usage.image_tokens,
                audio_tokens=response.usage.audio_tokens,
                cost_usd=response.usage.cost_usd,
                elapsed_sec=response.usage.elapsed_sec,
            )
            try:
                await asyncio.to_thread(usage_store.record, record)
            except Exception as exc:  # noqa: BLE001
                log.warning("ingest: usage_store.record failed: %s", exc)
        if not response.text or not response.text.strip():
            raise DocumentIngestError(f"LLM returned an empty response ingesting {path.name}")
        return response.text.strip()

    if len(numbered) <= MAX_ONESHOT_CHARS:
        card = await _call(f"Document: {path.name}\n\n{numbered}")
    else:
        chunks = _split_chunks(numbered, MAX_ONESHOT_CHARS)
        log.info(
            "ingest: %s is %d chars, map-reduce over %d chunks",
            path.name, len(numbered), len(chunks),
        )
        partials = []
        for i, chunk in enumerate(chunks, 1):
            partials.append(await _call(
                f"Document: {path.name} — part {i} of {len(chunks)} "
                f"(line numbers are absolute)\n\n{chunk}"
            ))
        joined = "\n\n---\n\n".join(partials)
        card = await _call(f"{_MERGE_PROMPT}\nDocument: {path.name}\n\n{joined}")

    cpath.write_text(card)
    log.info("ingest: wrote card %s (%d chars) as %s", cpath, len(card), session_id)
    return card
