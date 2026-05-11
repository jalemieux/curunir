#!/usr/bin/env python3
"""SEC EDGAR driver CLI.

Hits the public EDGAR APIs directly via httpx. SEC requires every request to
include a User-Agent that identifies the caller — set SEC_USER_AGENT in .env.

Every subcommand prints JSON to stdout. Errors print
``{"error": "...", "hint": "..."}`` and exit 1; usage errors exit 2.

The agent invokes this via the ``bash`` tool. Tests import it as a module
and call ``cmd_*`` functions directly.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any

import httpx

DATA_BASE = "https://data.sec.gov"
WWW_BASE = "https://www.sec.gov"
DEFAULT_TIMEOUT = 30
DEFAULT_USER_AGENT = "curunir/0.1 (admin@example.com)"
TICKER_INDEX_URL = "https://www.sec.gov/files/company_tickers.json"

# Cache the ticker→CIK index for the lifetime of this process. The file is
# small (~1 MB) and the index changes only when companies list/delist.
_TICKER_CACHE: dict[str, dict[str, str]] | None = None


def _user_agent() -> str:
    return os.environ.get("SEC_USER_AGENT", "").strip() or DEFAULT_USER_AGENT


def _client() -> httpx.Client:
    return httpx.Client(timeout=DEFAULT_TIMEOUT, headers={"User-Agent": _user_agent(), "Accept-Encoding": "gzip, deflate"})


def _get_json(client: httpx.Client, url: str) -> dict:
    r = client.get(url)
    if r.status_code == 403:
        raise RuntimeError("SEC returned 403 — set SEC_USER_AGENT in .env to <app>/<ver> (<email>)")
    r.raise_for_status()
    return r.json()


def _get_text(client: httpx.Client, url: str) -> str:
    r = client.get(url)
    if r.status_code == 403:
        raise RuntimeError("SEC returned 403 — set SEC_USER_AGENT in .env to <app>/<ver> (<email>)")
    r.raise_for_status()
    return r.text


def _load_ticker_index(client: httpx.Client) -> dict[str, dict[str, str]]:
    """Return a {TICKER: {cik, name}} map."""
    global _TICKER_CACHE
    if _TICKER_CACHE is not None:
        return _TICKER_CACHE
    data = _get_json(client, TICKER_INDEX_URL)
    out: dict[str, dict[str, str]] = {}
    # SEC ships this as a dict keyed by string indices.
    rows = data.values() if isinstance(data, dict) else data
    for row in rows:
        ticker = (row.get("ticker") or "").upper()
        cik = str(row.get("cik_str") or "").zfill(10)
        name = row.get("title") or ""
        if ticker and cik:
            out[ticker] = {"cik": cik, "name": name}
    _TICKER_CACHE = out
    return out


def _resolve_cik(ticker_or_cik: str, client: httpx.Client) -> tuple[str, str]:
    """Accept a ticker (LLY) or zero-padded CIK; return (cik, name)."""
    s = ticker_or_cik.strip()
    if s.isdigit():
        cik = s.zfill(10)
        return cik, ""
    idx = _load_ticker_index(client)
    row = idx.get(s.upper())
    if not row:
        raise LookupError(f"ticker not found in SEC index: {ticker_or_cik}")
    return row["cik"], row["name"]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_lookup(ticker: str, client: httpx.Client | None = None) -> dict:
    own = client is None
    if own:
        client = _client()
    try:
        cik, name = _resolve_cik(ticker, client)
        return {"ticker": ticker.upper(), "cik": cik, "name": name}
    finally:
        if own:
            client.close()


def cmd_facts(ticker: str, concept: str | None = None, client: httpx.Client | None = None) -> dict:
    own = client is None
    if own:
        client = _client()
    try:
        cik, name = _resolve_cik(ticker, client)
        if concept:
            # Single-concept endpoint: faster, smaller, taxonomy-aware.
            url = f"{DATA_BASE}/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json"
            try:
                data = _get_json(client, url)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise LookupError(f"concept '{concept}' not reported by {ticker.upper()} (try a different concept; see SKILL.md reference)")
                raise
            units = data.get("units") or {}
            # Collapse to a flat list with unit tags.
            facts = []
            for unit, rows in units.items():
                for row in rows:
                    facts.append({
                        "fy": row.get("fy"),
                        "fp": row.get("fp"),
                        "form": row.get("form"),
                        "filed": row.get("filed"),
                        "end": row.get("end"),
                        "start": row.get("start"),
                        "value": row.get("val"),
                        "unit": unit,
                        "accession": row.get("accn"),
                    })
            facts.sort(key=lambda f: (f.get("end") or "", f.get("filed") or ""))
            return {
                "ticker": ticker.upper(),
                "cik": cik,
                "name": name,
                "concept": concept,
                "label": data.get("label"),
                "description": data.get("description"),
                "count": len(facts),
                "facts": facts,
            }
        # Full company-facts blob.
        url = f"{DATA_BASE}/api/xbrl/companyfacts/CIK{cik}.json"
        data = _get_json(client, url)
        # List the available concepts so the agent can pick one.
        gaap = (data.get("facts") or {}).get("us-gaap") or {}
        concepts = sorted(gaap.keys())
        return {
            "ticker": ticker.upper(),
            "cik": cik,
            "name": name,
            "available_concepts": concepts,
            "hint": "re-run with --concept <Name> for the actual numbers",
        }
    finally:
        if own:
            client.close()


def cmd_filings(ticker: str, form_type: str | None = None, limit: int = 10, client: httpx.Client | None = None) -> dict:
    own = client is None
    if own:
        client = _client()
    try:
        cik, name = _resolve_cik(ticker, client)
        url = f"{DATA_BASE}/submissions/CIK{cik}.json"
        data = _get_json(client, url)
        recent = ((data.get("filings") or {}).get("recent") or {})
        forms = recent.get("form") or []
        accs = recent.get("accessionNumber") or []
        dates = recent.get("filingDate") or []
        primary_docs = recent.get("primaryDocument") or []
        primary_descs = recent.get("primaryDocDescription") or []
        items: list[dict] = []
        for i, f in enumerate(forms):
            if form_type and f.upper() != form_type.upper():
                continue
            acc = accs[i]
            acc_nodash = acc.replace("-", "")
            primary = primary_docs[i] if i < len(primary_docs) else ""
            url_doc = f"{WWW_BASE}/Archives/edgar/data/{int(cik)}/{acc_nodash}/{primary}" if primary else ""
            items.append({
                "form": f,
                "filed": dates[i],
                "accession": acc,
                "primary_document": primary,
                "primary_description": primary_descs[i] if i < len(primary_descs) else "",
                "url": url_doc,
            })
            if len(items) >= limit:
                break
        return {
            "ticker": ticker.upper(),
            "cik": cik,
            "name": name,
            "form_type": form_type,
            "count": len(items),
            "filings": items,
        }
    finally:
        if own:
            client.close()


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    # Drop scripts/styles entirely.
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)
    # Replace block tags with newlines so paragraph structure survives.
    html = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|br)>", "\n", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = _TAG_RE.sub("", html)
    # Collapse runs of whitespace inside lines, preserve newlines.
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def cmd_fetch(accession: str, client: httpx.Client | None = None) -> dict:
    """Fetch the primary document for a given accession.

    The submissions index is keyed by accession; we resolve the primary
    document URL via the company's submissions JSON. Accession format:
    ``0000059478-24-000006`` (with dashes) or the no-dash variant.
    """
    own = client is None
    if own:
        client = _client()
    try:
        # Pull the index file for this accession to find the primary doc.
        acc = accession.strip()
        if "-" not in acc:
            # Reconstruct dashes from a 18-char no-dash CIK style.
            if len(acc) == 18:
                acc = f"{acc[:10]}-{acc[10:12]}-{acc[12:]}"
        acc_nodash = acc.replace("-", "")
        # The index.json lives at /Archives/edgar/data/<int(cik)>/<acc_nodash>/index.json
        # We don't know the CIK from the accession alone; the first 10 digits
        # of the accession are the filer CIK (zero-padded but commonly stripped).
        cik_str = acc.split("-")[0]
        cik_int = str(int(cik_str))
        index_url = f"{WWW_BASE}/Archives/edgar/data/{cik_int}/{acc_nodash}/index.json"
        idx = _get_json(client, index_url)
        items = ((idx.get("directory") or {}).get("item")) or []
        primary = None
        # The primary doc is normally the first .htm file that isn't an index.
        for it in items:
            name = it.get("name") or ""
            if name.endswith((".htm", ".html")) and not name.startswith("index"):
                primary = name
                break
        if primary is None:
            raise LookupError(f"no primary document found for accession {acc}")
        doc_url = f"{WWW_BASE}/Archives/edgar/data/{cik_int}/{acc_nodash}/{primary}"
        # Tiny pause — SEC asks callers to throttle to ~10 req/s.
        time.sleep(0.1)
        html = _get_text(client, doc_url)
        text = _strip_html(html)
        return {
            "accession": acc,
            "url": doc_url,
            "primary_document": primary,
            "char_count": len(text),
            "text": text,
        }
    finally:
        if own:
            client.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="edgar.py", description="SEC EDGAR driver.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("lookup")
    sp.add_argument("ticker")

    sp = sub.add_parser("facts")
    sp.add_argument("ticker")
    sp.add_argument("--concept", default=None)

    sp = sub.add_parser("filings")
    sp.add_argument("ticker")
    sp.add_argument("--type", dest="form_type", default=None)
    sp.add_argument("--limit", type=int, default=10)

    sp = sub.add_parser("fetch")
    sp.add_argument("accession")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2

    try:
        if args.cmd == "lookup":
            result = cmd_lookup(args.ticker)
        elif args.cmd == "facts":
            result = cmd_facts(args.ticker, concept=args.concept)
        elif args.cmd == "filings":
            result = cmd_filings(args.ticker, form_type=args.form_type, limit=args.limit)
        elif args.cmd == "fetch":
            result = cmd_fetch(args.accession)
        else:
            print(json.dumps({"error": f"unknown subcommand: {args.cmd}"}))
            return 2
    except LookupError as e:
        print(json.dumps({"error": str(e), "hint": "use `lookup` first to verify the ticker, or check the concept name in SKILL.md"}))
        return 1
    except RuntimeError as e:
        print(json.dumps({"error": str(e), "hint": "set SEC_USER_AGENT=<app>/<ver> (<email>) in .env"}))
        return 1
    except httpx.HTTPError as e:
        print(json.dumps({"error": f"HTTP error: {e}", "hint": "SEC may be rate-limiting; wait a few seconds and retry"}))
        return 1
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        return 1
    print(json.dumps(result, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
