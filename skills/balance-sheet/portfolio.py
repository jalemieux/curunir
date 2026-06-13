#!/usr/bin/env python3
"""Balance-sheet CLI — thin adapter over src.portfolio.engine.

Every subcommand prints JSON to stdout. Errors print {"error","hint"} and
exit 1. The engine owns all logic; this file only parses args and serializes.
Default store: context/memory/portfolio.db (override with --db)."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from src.portfolio import db as pdb       # noqa: E402
from src.portfolio import engine          # noqa: E402

DEFAULT_DB = "context/memory/portfolio.db"


def _kv(pairs: list[str]) -> dict:
    """Parse `key=value` pairs (for `set`)."""
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        out[k] = v
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="portfolio.py", description="Balance-sheet store.")
    p.add_argument("--db", default=DEFAULT_DB)
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("networth", "rollup"):
        sub.add_parser(name)
    sp = sub.add_parser("list"); sp.add_argument("--class", dest="cls"); sp.add_argument("--account")
    sp = sub.add_parser("show"); sp.add_argument("id")
    sp = sub.add_parser("re-equity"); sp.add_argument("property_id")
    sp = sub.add_parser("pnl"); sp.add_argument("--class", dest="cls", default="collectible")
    sp = sub.add_parser("query"); sp.add_argument("sql")
    sp = sub.add_parser("refresh")
    sp = sub.add_parser("render")

    sp = sub.add_parser("add")
    for f in ("class", "label", "ticker", "account", "acquired"):
        sp.add_argument(f"--{f}")
    for f in ("qty", "avg-cost", "cost-basis", "value"):
        sp.add_argument(f"--{f}", type=float)
    sp = sub.add_parser("set"); sp.add_argument("id"); sp.add_argument("pairs", nargs="+")
    sp = sub.add_parser("rm"); sp.add_argument("id")
    sp = sub.add_parser("add-liability")
    for f in ("class", "label", "linked-asset"):
        sp.add_argument(f"--{f}")
    for f in ("balance", "apr"):
        sp.add_argument(f"--{f}", type=float)
    sp = sub.add_parser("import-rows")
    sp.add_argument("--rows-file", required=True, help="path to a JSON array of asset rows")
    sp.add_argument("--account")
    sp.add_argument("--stated-total", type=float)

    sp = sub.add_parser("buy")
    for f in ("ticker", "trade-date", "class", "account", "label", "note"):
        sp.add_argument(f"--{f}")
    for f in ("qty", "price", "fees"):
        sp.add_argument(f"--{f}", type=float)
    sp = sub.add_parser("sell")
    sp.add_argument("asset_id")
    sp.add_argument("--trade-date")
    sp.add_argument("--note")
    for f in ("qty", "price", "fees"):
        sp.add_argument(f"--{f}", type=float)
    sp = sub.add_parser("trades")
    for f in ("ticker", "account", "side", "since"):
        sp.add_argument(f"--{f}")
    sp = sub.add_parser("realized")
    for f in ("ticker", "account"):
        sp.add_argument(f"--{f}")
    sp.add_argument("--year", type=int)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    pdb.init_db(args.db)
    db = args.db
    try:
        if args.cmd == "networth": out = engine.networth(db)
        elif args.cmd == "rollup": out = engine.rollup(db)
        elif args.cmd == "list": out = engine.list_assets(db, cls=args.cls, account=args.account)
        elif args.cmd == "show": out = engine.show(db, args.id)
        elif args.cmd == "re-equity": out = engine.re_equity(db, args.property_id)
        elif args.cmd == "pnl": out = engine.pnl(db, cls=args.cls)
        elif args.cmd == "query": out = engine.query(db, args.sql)
        elif args.cmd == "refresh": out = engine.refresh(db)
        elif args.cmd == "render": out = {"markdown": engine.render_markdown(db)}
        elif args.cmd == "add":
            fields = {"class": getattr(args, "class"), "label": args.label,
                      "ticker": args.ticker, "account": args.account,
                      "acquired": args.acquired, "qty": args.qty,
                      "avg_cost": args.avg_cost, "cost_basis": args.cost_basis,
                      "value": args.value}
            out = engine.add_asset(db, {k: v for k, v in fields.items() if v is not None})
        elif args.cmd == "set": out = engine.update_asset(db, args.id, _kv(args.pairs))
        elif args.cmd == "rm": out = engine.remove_asset(db, args.id)
        elif args.cmd == "add-liability":
            fields = {"class": getattr(args, "class"), "label": args.label,
                      "balance": args.balance, "apr": args.apr,
                      "linked_asset": args.linked_asset}
            out = engine.add_liability(db, {k: v for k, v in fields.items() if v is not None})
        elif args.cmd == "import-rows":
            with open(args.rows_file) as f:
                rows = json.load(f)
            out = engine.import_rows(db, rows, account=args.account,
                                     stated_total=args.stated_total)
        elif args.cmd == "buy":
            fields = {"ticker": args.ticker, "trade_date": args.trade_date,
                      "class": getattr(args, "class"), "account": args.account,
                      "label": args.label, "note": args.note, "qty": args.qty,
                      "price": args.price, "fees": args.fees}
            out = engine.record_buy(db, {k: v for k, v in fields.items() if v is not None})
        elif args.cmd == "sell":
            fields = {"asset_id": args.asset_id, "trade_date": args.trade_date,
                      "note": args.note, "qty": args.qty, "price": args.price,
                      "fees": args.fees}
            out = engine.record_sell(db, {k: v for k, v in fields.items() if v is not None})
        elif args.cmd == "trades":
            out = engine.trade_history(db, ticker=args.ticker, account=args.account,
                                       side=args.side, since=args.since)
        elif args.cmd == "realized":
            out = engine.realized_pnl(db, ticker=args.ticker, account=args.account,
                                      year=args.year)
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
