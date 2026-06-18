#!/usr/bin/env python3
"""Financial-plan CLI — thin adapter over src.financial_plan.engine.

Every subcommand prints JSON to stdout. Errors print {"error","hint"} and exit
1. The engine owns all logic; this file only parses args and serializes.

The engine is stateless (no DB of its own). `--from-portfolio` is the one
composition convenience: it reads the *starting balance* from the balance-sheet
store's net worth (`src.portfolio.engine.networth`) so a plan starts from the
owner's real book rather than an invented number. Default store:
context/memory/portfolio.db (override with --db)."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from src.financial_plan import engine          # noqa: E402

DEFAULT_DB = "context/memory/portfolio.db"


def _add_inputs(sp: argparse.ArgumentParser) -> None:
    """Plan-input flags shared by every subcommand."""
    for f in ("current-age", "retirement-age", "end-age"):
        sp.add_argument(f"--{f}", type=int)
    for f in ("current-balance", "annual-contribution", "annual-spending",
              "real-return", "volatility"):
        sp.add_argument(f"--{f}", type=float)
    sp.add_argument("--from-portfolio", action="store_true",
                    help="seed current-balance from the balance-sheet net worth")
    sp.add_argument("--db", default=DEFAULT_DB,
                    help="portfolio store for --from-portfolio")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="plan.py", description="Retirement/goal projection.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("project", "montecarlo", "stress", "report"):
        sp = sub.add_parser(name)
        _add_inputs(sp)
        if name != "project":
            sp.add_argument("--seed", type=int, default=0)
            sp.add_argument("--n-sims", type=int, default=1000)
    return p


def _inputs_from_args(args) -> dict:
    fields = {
        "current_age": args.current_age,
        "retirement_age": args.retirement_age,
        "end_age": args.end_age,
        "current_balance": args.current_balance,
        "annual_contribution": args.annual_contribution,
        "annual_spending": args.annual_spending,
        "real_return": args.real_return,
        "volatility": args.volatility,
    }
    if args.from_portfolio:
        from src.portfolio import db as pdb
        from src.portfolio import engine as pengine
        pdb.init_db(args.db)
        fields["current_balance"] = pengine.networth(args.db)["net_worth"]
    return {k: v for k, v in fields.items() if v is not None}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        inputs = _inputs_from_args(args)
        if args.cmd == "project":
            out = engine.project(inputs)
        elif args.cmd == "montecarlo":
            out = engine.monte_carlo(inputs, n_sims=args.n_sims, seed=args.seed)
        elif args.cmd == "stress":
            out = engine.stress_grid(inputs, n_sims=args.n_sims, seed=args.seed)
        elif args.cmd == "report":
            out = {"markdown": engine.render_markdown(
                inputs, n_sims=args.n_sims, seed=args.seed)}
        else:
            raise ValueError(f"unknown command {args.cmd!r}")
    except Exception as e:  # noqa: BLE001 — surface as JSON, not a traceback
        print(json.dumps({"error": str(e),
                          "hint": "check the input flags; see SKILL.md"}))
        return 1
    print(json.dumps(out, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
