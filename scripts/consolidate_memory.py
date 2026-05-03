"""One-shot CLI: consolidate duplicate/correcting sections in a memory file via LLM.

Usage:
    python scripts/consolidate_memory.py [--file tasks.md] [--memory-dir context/memory] [--dry-run]

The script reads the target memory file, asks the LLM to merge duplicate or
correcting `## Topic` sections (keeping the latest fact and a chronological
note of source lines), and writes back the consolidated file. With --dry-run,
the consolidated text is printed to stdout and the file is left untouched.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Allow `python scripts/consolidate_memory.py` from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

from src.config import AgentConfig
from src.llm import call_llm

CONSOLIDATION_PROMPT = """\
You are a memory consolidation system. The markdown file below contains durable facts organized as `## Topic` H2 sections. Some topics may be repeated across multiple sections — duplicate or correcting entries that accumulated over time.

Your task: produce a consolidated version of the file where each topic appears exactly once, retaining only the most current fact. Preserve a brief chronological history of any **Source:** lines so the audit trail is not lost.

Rules:
- Output ONLY the consolidated markdown content. No commentary, no explanation, no code fences.
- Preserve every distinct topic. Only merge sections that refer to the same topic (identical or near-identical headings).
- For each merged topic, keep the latest **Fact:** line. Combine **Source:** lines as a chronological list when multiple existed.
- Preserve any preamble or text that appears before the first `## ` heading.
- Use `## ` (H2) headings exactly as in the input.
"""


def _strip_fences(text: str) -> str:
    """Strip a leading/trailing ``` code fence if the LLM wrapped its output."""
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    # Drop opening fence (e.g., ```markdown or ```)
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    # Drop closing fence
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


async def consolidate(file_path: Path, config: AgentConfig, dry_run: bool = False) -> str:
    """Consolidate duplicate sections in `file_path`. Returns the new text."""
    text = file_path.read_text()
    messages = [
        {"role": "system", "content": CONSOLIDATION_PROMPT},
        {"role": "user", "content": text},
    ]
    response = await call_llm(
        config.model,
        messages,
        tools=[],
        api_base=config.api_base,
        openrouter_provider=config.openrouter_provider,
    )
    consolidated = _strip_fences(response.text or "")

    if not dry_run:
        if not consolidated.endswith("\n"):
            consolidated += "\n"
        file_path.write_text(consolidated)
    return consolidated


def _build_config() -> AgentConfig:
    kwargs: dict = {}
    if model := os.environ.get("MODEL"):
        kwargs["model"] = model
    if api_base := os.environ.get("API_BASE"):
        kwargs["api_base"] = api_base
    if provider := os.environ.get("OPENROUTER_PROVIDER"):
        kwargs["openrouter_provider"] = provider
    return AgentConfig(**kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--file",
        default="tasks.md",
        help="Memory file path relative to --memory-dir (default: tasks.md)",
    )
    parser.add_argument(
        "--memory-dir",
        default="context/memory",
        help="Memory directory (default: context/memory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print consolidated content to stdout instead of overwriting the file",
    )
    args = parser.parse_args(argv)

    load_dotenv()

    target = Path(args.memory_dir) / args.file
    if not target.exists():
        print(f"Error: {target} does not exist", file=sys.stderr)
        return 1

    config = _build_config()

    consolidated = asyncio.run(consolidate(target, config, dry_run=args.dry_run))

    if args.dry_run:
        sys.stdout.write(consolidated)
        if not consolidated.endswith("\n"):
            sys.stdout.write("\n")
    else:
        print(f"Wrote consolidated content to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
