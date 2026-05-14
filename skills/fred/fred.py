#!/usr/bin/env python3
"""FRED (St. Louis Fed) driver CLI.

Hits the public FRED API directly via httpx — no third-party SDK needed.
Every subcommand prints JSON to stdout. Errors print
``{"error": "...", "hint": "..."}`` and exit 1; usage errors exit 2.

The agent invokes this via the ``bash`` tool. Tests import it as a module
and call ``cmd_*`` functions directly with a custom ``client`` for mocking.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

API_BASE = "https://api.stlouisfed.org/fred"
DEFAULT_TIMEOUT = 30


def _api_key() -> str:
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "FRED_API_KEY not set. Get a free key at "
            "https://fred.stlouisfed.org/docs/api/api_key.html and add it to .env"
        )
    return key


def _client() -> httpx.Client:
    return httpx.Client(timeout=DEFAULT_TIMEOUT)


def _get(path: str, params: dict, client: httpx.Client | None = None) -> dict:
    own = client is None
    if own:
        client = _client()
    try:
        params = {**params, "api_key": _api_key(), "file_type": "json"}
        r = client.get(f"{API_BASE}{path}", params=params)
        r.raise_for_status()
        return r.json()
    finally:
        if own:
            client.close()


def _series_info(series_id: str, client: httpx.Client | None = None) -> dict:
    data = _get("/series", {"series_id": series_id}, client=client)
    arr = data.get("seriess") or []
    if not arr:
        raise LookupError(f"series not found: {series_id}")
    return arr[0]


def _parse_value(v: str) -> float | None:
    if v is None or v == "." or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_latest(series_id: str, client: httpx.Client | None = None) -> dict:
    info = _series_info(series_id, client=client)
    obs = _get(
        "/series/observations",
        {"series_id": series_id, "sort_order": "desc", "limit": 10},
        client=client,
    )
    rows = obs.get("observations") or []
    # Walk back until we find one with a value (FRED returns "." for missing).
    latest = None
    for row in rows:
        val = _parse_value(row.get("value"))
        if val is not None:
            latest = {"date": row["date"], "value": val}
            break
    if latest is None:
        raise LookupError(f"no recent observations for series: {series_id}")
    return {
        "id": series_id,
        "title": info.get("title"),
        "units": info.get("units"),
        "frequency": info.get("frequency"),
        "as_of": latest["date"],
        "value": latest["value"],
    }


def cmd_series(
    series_id: str,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    client: httpx.Client | None = None,
) -> dict:
    info = _series_info(series_id, client=client)
    params: dict[str, Any] = {"series_id": series_id, "sort_order": "asc"}
    if start:
        params["observation_start"] = start
    if end:
        params["observation_end"] = end
    if limit:
        params["limit"] = limit
    obs = _get("/series/observations", params, client=client)
    rows = [
        {"date": r["date"], "value": _parse_value(r.get("value"))}
        for r in (obs.get("observations") or [])
    ]
    return {
        "id": series_id,
        "title": info.get("title"),
        "units": info.get("units"),
        "frequency": info.get("frequency"),
        "start": start,
        "end": end,
        "count": len(rows),
        "observations": rows,
    }


def cmd_search(query: str, limit: int = 10, client: httpx.Client | None = None) -> dict:
    data = _get(
        "/series/search",
        {"search_text": query, "limit": limit, "order_by": "popularity", "sort_order": "desc"},
        client=client,
    )
    items = []
    for s in data.get("seriess") or []:
        items.append({
            "id": s.get("id"),
            "title": s.get("title"),
            "units": s.get("units"),
            "frequency": s.get("frequency"),
            "last_updated": s.get("last_updated"),
            "popularity": s.get("popularity"),
        })
    return {"query": query, "count": len(items), "results": items}


def cmd_info(series_id: str, client: httpx.Client | None = None) -> dict:
    info = _series_info(series_id, client=client)
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "notes": info.get("notes"),
        "units": info.get("units"),
        "frequency": info.get("frequency"),
        "seasonal_adjustment": info.get("seasonal_adjustment"),
        "observation_start": info.get("observation_start"),
        "observation_end": info.get("observation_end"),
        "last_updated": info.get("last_updated"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fred.py", description="FRED driver.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("latest")
    sp.add_argument("series_id")

    sp = sub.add_parser("series")
    sp.add_argument("series_id")
    sp.add_argument("--start", default=None)
    sp.add_argument("--end", default=None)
    sp.add_argument("--limit", type=int, default=None)

    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=10)

    sp = sub.add_parser("info")
    sp.add_argument("series_id")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2

    try:
        if args.cmd == "latest":
            result = cmd_latest(args.series_id)
        elif args.cmd == "series":
            result = cmd_series(args.series_id, start=args.start, end=args.end, limit=args.limit)
        elif args.cmd == "search":
            result = cmd_search(args.query, limit=args.limit)
        elif args.cmd == "info":
            result = cmd_info(args.series_id)
        else:
            print(json.dumps({"error": f"unknown subcommand: {args.cmd}"}))
            return 2
    except LookupError as e:
        print(json.dumps({"error": str(e), "hint": "use `search` to find the right series ID"}))
        return 1
    except RuntimeError as e:
        print(json.dumps({"error": str(e), "hint": "set FRED_API_KEY in .env"}))
        return 1
    except httpx.HTTPError as e:
        print(json.dumps({"error": f"HTTP error: {e}", "hint": "check network and FRED_API_KEY"}))
        return 1
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        return 1
    print(json.dumps(result, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
