#!/usr/bin/env python3
"""Document-ingest CLI — thin adapter over src.document_ingest.ingest_document.

Prints the document card (markdown) to stdout; errors go to stderr with exit 1.
An existing `<path>.card.md` is reused without an LLM call, so re-running is
free. Usage is recorded under `ingest:<hash>` in the usage db.

Usage: python skills/document-ingest/ingest.py <path> [--usage-db PATH]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from dotenv import load_dotenv                                    # noqa: E402

from src.config import AgentConfig                                # noqa: E402
from src.document_ingest import DocumentIngestError, ingest_document  # noqa: E402
from src.usage_store import UsageStore                            # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Produce a document card for a file.")
    parser.add_argument("path", help="Document to ingest (text, PDF, DOCX, XLSX, CSV).")
    parser.add_argument(
        "--usage-db", default="./context/usage.db",
        help="Usage-tracking SQLite db (default: ./context/usage.db).",
    )
    args = parser.parse_args(argv)

    load_dotenv()
    model = os.environ.get("MODEL")
    api_base = os.environ.get("API_BASE")
    openrouter_provider = os.environ.get("OPENROUTER_PROVIDER")
    config = AgentConfig(
        **({"model": model} if model else {}),
        **({"api_base": api_base} if api_base else {}),
        **({"openrouter_provider": openrouter_provider} if openrouter_provider else {}),
    )

    usage_store = UsageStore(args.usage_db)
    try:
        card = asyncio.run(ingest_document(args.path, config, usage_store=usage_store))
    except DocumentIngestError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(card)
    return 0


if __name__ == "__main__":
    sys.exit(main())
