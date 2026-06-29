# src/usage.py
"""CLI for inspecting persisted LLM usage.

Usage:
    python -m src.usage [--window 1d|7d|30d|24h|...] [--by model|day|session] [--db PATH]
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import timedelta
from pathlib import Path

from src.config import AgentConfig
from src.usage_store import UsageStore


_WINDOW_RE = re.compile(r"^(\d+)\s*([dhm])$")


def _parse_window(s: str) -> timedelta:
    m = _WINDOW_RE.match(s.strip().lower())
    if not m:
        raise argparse.ArgumentTypeError(f"window must look like '7d', '24h', or '30m', got {s!r}")
    n, unit = int(m.group(1)), m.group(2)
    return {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]


def _format_table(rows: list[dict], group_by: str) -> str:
    if not rows:
        return "(no usage in window)"

    key = group_by if group_by in ("day", "session") else "model"
    headers = [
        key, "calls", "prompt", "completion", "cached", "reasoning", "cost_usd", "elapsed_s",
    ]
    formatted = []
    for r in rows:
        formatted.append([
            str(r.get(key, "")),
            str(r["calls"]),
            f"{r['prompt_tokens']:,}",
            f"{r['completion_tokens']:,}",
            f"{r['cached_prompt_tokens']:,}",
            f"{r['reasoning_tokens']:,}",
            f"${r['cost_usd']:.4f}",
            f"{r['elapsed_sec']:.1f}",
        ])

    widths = [max(len(h), *(len(row[i]) for row in formatted)) for i, h in enumerate(headers)]
    sep = "  ".join("-" * w for w in widths)
    head = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    body = "\n".join(
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in formatted
    )

    # Totals row
    totals = {
        "calls": sum(r["calls"] for r in rows),
        "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
        "completion_tokens": sum(r["completion_tokens"] for r in rows),
        "cached_prompt_tokens": sum(r["cached_prompt_tokens"] for r in rows),
        "reasoning_tokens": sum(r["reasoning_tokens"] for r in rows),
        "cost_usd": sum(r["cost_usd"] for r in rows),
        "elapsed_sec": sum(r["elapsed_sec"] for r in rows),
    }
    totals_row = [
        "TOTAL",
        str(totals["calls"]),
        f"{totals['prompt_tokens']:,}",
        f"{totals['completion_tokens']:,}",
        f"{totals['cached_prompt_tokens']:,}",
        f"{totals['reasoning_tokens']:,}",
        f"${totals['cost_usd']:.4f}",
        f"{totals['elapsed_sec']:.1f}",
    ]
    totals_line = "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(totals_row))

    return "\n".join([head, sep, body, sep, totals_line])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.usage", description=__doc__)
    parser.add_argument("--window", type=_parse_window, default=_parse_window("7d"),
                        help="Lookback window (e.g. 1d, 7d, 30d, 24h). Default 7d.")
    parser.add_argument("--by", choices=["model", "day", "session"], default="model")
    parser.add_argument("--db", type=Path, default=None,
                        help=f"SQLite path (default {AgentConfig().usage_db}).")
    args = parser.parse_args(argv)

    db_path = args.db or AgentConfig().usage_db
    if not db_path.exists():
        print(f"No usage database at {db_path}.", file=sys.stderr)
        return 1
    store = UsageStore(db_path)
    rows = store.summary(args.window, group_by=args.by)
    print(_format_table(rows, args.by))
    return 0


if __name__ == "__main__":
    sys.exit(main())
