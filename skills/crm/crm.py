#!/usr/bin/env python3
"""CRM CLI — thin adapter over src.crm.engine.

Every subcommand prints JSON to stdout. Errors print {"error","hint"} and
exit 1. The engine owns all logic; this file only parses args and serializes.
Default store: context/memory/crm.db (override with --db). Mirrors
skills/balance-sheet/portfolio.py."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from src.crm import db as cdb          # noqa: E402
from src.crm import engine             # noqa: E402

DEFAULT_DB = "context/memory/crm.db"


def _kv(pairs: list[str]) -> dict:
    """Parse `key=value` pairs (for `set`)."""
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        out[k] = v
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="crm.py", description="Mini-CRM store.")
    p.add_argument("--db", default=DEFAULT_DB)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("add")
    for f in ("name", "email", "company", "source", "stage", "owner", "note"):
        sp.add_argument(f"--{f}")
    sp = sub.add_parser("set"); sp.add_argument("id"); sp.add_argument("pairs", nargs="+")
    sp = sub.add_parser("set-stage"); sp.add_argument("id"); sp.add_argument("stage")
    sp = sub.add_parser("rm"); sp.add_argument("id")
    sp = sub.add_parser("log")
    sp.add_argument("lead_id")
    sp.add_argument("--kind", required=True)
    sp.add_argument("--body")
    sp.add_argument("--occurred-at")
    sp = sub.add_parser("list")
    for f in ("stage", "source", "owner"):
        sp.add_argument(f"--{f}")
    sp = sub.add_parser("show"); sp.add_argument("id")
    sp = sub.add_parser("pipeline")
    sp = sub.add_parser("activity")
    sp.add_argument("--lead-id"); sp.add_argument("--since")
    sp.add_argument("--limit", type=int)
    sp = sub.add_parser("query"); sp.add_argument("sql")
    sp = sub.add_parser("render")
    sp = sub.add_parser("import-rows")
    sp.add_argument("--rows-file", required=True, help="path to a JSON array of lead rows")
    sp.add_argument("--source"); sp.add_argument("--owner")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cdb.init_db(args.db)
    db = args.db
    try:
        if args.cmd == "add":
            fields = {"name": args.name, "email": args.email,
                      "company": args.company, "source": args.source,
                      "stage": args.stage, "owner": args.owner, "note": args.note}
            out = engine.add_lead(db, {k: v for k, v in fields.items() if v is not None})
        elif args.cmd == "set":
            out = engine.update_lead(db, args.id, _kv(args.pairs))
        elif args.cmd == "set-stage":
            out = engine.set_stage(db, args.id, args.stage)
        elif args.cmd == "rm":
            out = engine.remove_lead(db, args.id)
        elif args.cmd == "log":
            fields = {"lead_id": args.lead_id, "kind": args.kind,
                      "body": args.body, "occurred_at": args.occurred_at}
            out = engine.log_interaction(db, {k: v for k, v in fields.items() if v is not None})
        elif args.cmd == "list":
            out = engine.list_leads(db, stage=args.stage, source=args.source,
                                    owner=args.owner)
        elif args.cmd == "show":
            out = engine.show(db, args.id)
        elif args.cmd == "pipeline":
            out = engine.pipeline(db)
        elif args.cmd == "activity":
            out = engine.activity(db, lead_id=args.lead_id, since=args.since,
                                  limit=args.limit)
        elif args.cmd == "query":
            out = engine.query(db, args.sql)
        elif args.cmd == "render":
            out = {"markdown": engine.render_markdown(db)}
        elif args.cmd == "import-rows":
            with open(args.rows_file) as f:
                rows = json.load(f)
            out = engine.import_rows(db, rows, source=args.source, owner=args.owner)
        else:
            raise ValueError(f"unknown command {args.cmd!r}")
    except Exception as e:  # noqa: BLE001 — surface as JSON, not a traceback
        print(json.dumps({"error": str(e),
                          "hint": "check the field names / id; see SKILL.md"}))
        return 1
    print(json.dumps(out, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
