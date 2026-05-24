#!/usr/bin/env python3
"""Polymarket driver CLI.

Hits the public Polymarket Gamma API (no key required) via httpx. Every
subcommand prints JSON to stdout. Errors print
``{"error": "...", "hint": "..."}`` and exit 1; usage errors exit 2.

The agent invokes this via the ``bash`` tool. Tests import it as a module
and call ``cmd_*`` functions directly with a custom ``client`` for mocking.

Output is normalized to a common prediction-market shape so a future
``kalshi`` skill can adopt the same fields and downstream consumers don't
have to fork by venue:

    {
      "venue": "polymarket",
      "id": "<gamma market id>",
      "question": "...",
      "url": "https://polymarket.com/market/<slug>",
      "status": "active" | "closed" | "archived",
      "end_date": "YYYY-MM-DD" | null,
      "volume_usd": <float> | null,
      "outcomes": [{"name": "Yes", "price": 0.62, "implied_prob": 0.62}, ...],
      "fetched_at": "YYYY-MM-DDTHH:MM:SSZ"
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

import httpx

API_BASE = "https://gamma-api.polymarket.com"
MARKET_URL_BASE = "https://polymarket.com/market"
DEFAULT_TIMEOUT = 30
VENUE = "polymarket"


def _client() -> httpx.Client:
    return httpx.Client(timeout=DEFAULT_TIMEOUT)


def _get(path: str, params: dict, client: httpx.Client | None = None) -> Any:
    own = client is None
    if own:
        client = _client()
    try:
        r = client.get(f"{API_BASE}{path}", params=params)
        r.raise_for_status()
        return r.json()
    finally:
        if own:
            client.close()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_json_field(value: Any) -> list:
    """Gamma returns `outcomes` and `outcomePrices` as JSON-encoded strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _end_date(market: dict) -> str | None:
    iso = market.get("endDateIso")
    if iso:
        return iso
    raw = market.get("endDate")
    if not raw:
        return None
    # `endDate` looks like "2026-07-31T12:00:00Z" — keep just the date.
    return str(raw)[:10] or None


def _status(market: dict) -> str:
    if market.get("archived"):
        return "archived"
    if market.get("closed"):
        return "closed"
    if market.get("active"):
        return "active"
    return "inactive"


def _normalize_market(market: dict) -> dict:
    """Convert a raw Gamma market dict to the normalized output shape."""
    names = [str(n) for n in _parse_json_field(market.get("outcomes"))]
    prices = [_to_float(p) for p in _parse_json_field(market.get("outcomePrices"))]
    outcomes = []
    for i, name in enumerate(names):
        price = prices[i] if i < len(prices) else None
        outcomes.append({
            "name": name,
            "price": price,
            "implied_prob": price,  # Polymarket prices are 0-1 implied probabilities
        })

    volume = _to_float(market.get("volumeNum"))
    if volume is None:
        volume = _to_float(market.get("volume"))

    slug = market.get("slug")
    return {
        "venue": VENUE,
        "id": str(market.get("id")) if market.get("id") is not None else None,
        "question": market.get("question"),
        "slug": slug,
        "url": f"{MARKET_URL_BASE}/{slug}" if slug else None,
        "status": _status(market),
        "end_date": _end_date(market),
        "volume_usd": volume,
        "outcomes": outcomes,
        "fetched_at": _now(),
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_search(
    query: str,
    limit: int = 10,
    active_only: bool = False,
    client: httpx.Client | None = None,
) -> dict:
    data = _get(
        "/public-search",
        {"q": query, "limit_per_type": limit},
        client=client,
    )
    raw_markets: list[dict] = []
    seen: set[str] = set()
    for event in (data.get("events") or []):
        for m in (event.get("markets") or []):
            mid = str(m.get("id"))
            if mid in seen:
                continue
            seen.add(mid)
            raw_markets.append(m)

    if active_only:
        raw_markets = [
            m for m in raw_markets
            if m.get("active") and not m.get("closed") and not m.get("archived")
        ]

    results = [_normalize_market(m) for m in raw_markets[:limit]]
    return {"query": query, "count": len(results), "results": results}


def cmd_market(identifier: str, client: httpx.Client | None = None) -> dict:
    # Numeric → treat as gamma id, otherwise as slug.
    key = "id" if identifier.isdigit() else "slug"
    data = _get("/markets", {key: identifier}, client=client)
    items = data if isinstance(data, list) else []
    if not items:
        raise LookupError(f"market not found: {identifier}")
    return _normalize_market(items[0])


def cmd_trending(limit: int = 10, client: httpx.Client | None = None) -> dict:
    data = _get(
        "/markets",
        {
            "limit": limit,
            "active": "true",
            "closed": "false",
            "archived": "false",
            "order": "volume24hr",
            "ascending": "false",
        },
        client=client,
    )
    items = data if isinstance(data, list) else []
    results = [_normalize_market(m) for m in items[:limit]]
    return {"count": len(results), "results": results}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="polymarket.py", description="Polymarket driver.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("search", help="Search markets by free-text query.")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--active-only", action="store_true",
                    help="Exclude closed/archived markets.")

    sp = sub.add_parser("market", help="Fetch one market by slug or numeric id.")
    sp.add_argument("identifier")

    sp = sub.add_parser("trending", help="Top markets by 24h volume.")
    sp.add_argument("--limit", type=int, default=10)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2

    try:
        if args.cmd == "search":
            result = cmd_search(args.query, limit=args.limit, active_only=args.active_only)
        elif args.cmd == "market":
            result = cmd_market(args.identifier)
        elif args.cmd == "trending":
            result = cmd_trending(limit=args.limit)
        else:
            print(json.dumps({"error": f"unknown subcommand: {args.cmd}"}))
            return 2
    except LookupError as e:
        print(json.dumps({
            "error": str(e),
            "hint": "use `search` to find a valid slug or id",
        }))
        return 1
    except httpx.HTTPError as e:
        print(json.dumps({
            "error": f"HTTP error: {e}",
            "hint": "check network connectivity to gamma-api.polymarket.com",
        }))
        return 1
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        return 1

    print(json.dumps(result, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
