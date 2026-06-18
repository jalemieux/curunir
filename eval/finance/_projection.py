"""Anchor helper for the financial-plan eval family (projection tasks).

Reports the projection truth the graders anchor to by calling the SAME engine
(`src.financial_plan.engine`) the agent reaches through the `financial_plan`
tool / `plan.py` CLI — so grader and agent can't drift. Monte-Carlo is seeded
(default seed 0, n_sims 1000 — the tool's own defaults), so a fixed scenario
yields a reproducible success probability the harness tolerance-checks.

`--from-portfolio` seeds the starting balance from the same seeded SQLite store
the position-tracking anchors use (`CURUNIR_PORTFOLIO_DB`, default
context/memory/portfolio.db), mirroring the agent's `--from-portfolio`.

Subcommands (each prints one JSON object):

    python eval/finance/_projection.py montecarlo --current-age 60 ... [--seed 0 --n-sims 1000]
        -> {"success_prob", "success_pct", "threshold_met",
            "ending_balance_p50"}

    python eval/finance/_projection.py project --current-age 60 ...
        -> {"ending_balance", "depleted", "depletion_age"}
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.financial_plan import engine  # noqa: E402


def _inputs(args) -> dict:
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
        db = os.environ.get("CURUNIR_PORTFOLIO_DB",
                            str(Path(__file__).resolve().parents[2]
                                / "context" / "memory" / "portfolio.db"))
        pdb.init_db(db)
        fields["current_balance"] = pengine.networth(db)["net_worth"]
    return {k: v for k, v in fields.items() if v is not None}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="_projection.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("montecarlo", "project"):
        sp = sub.add_parser(name)
        for f in ("current-age", "retirement-age", "end-age"):
            sp.add_argument(f"--{f}", type=int)
        for f in ("current-balance", "annual-contribution", "annual-spending",
                  "real-return", "volatility"):
            sp.add_argument(f"--{f}", type=float)
        sp.add_argument("--from-portfolio", action="store_true")
        if name == "montecarlo":
            sp.add_argument("--seed", type=int, default=0)
            sp.add_argument("--n-sims", type=int, default=1000)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    inputs = _inputs(args)
    if args.cmd == "montecarlo":
        mc = engine.monte_carlo(inputs, n_sims=args.n_sims, seed=args.seed)
        out = {
            "success_prob": mc["success_prob"],
            "success_pct": round(mc["success_prob"] * 100, 1),
            "threshold_met": mc["threshold_met"],
            "ending_balance_p50": mc["ending_balance_p50"],
        }
    else:
        pr = engine.project(inputs)
        out = {"ending_balance": pr["ending_balance"],
               "depleted": pr["depleted"], "depletion_age": pr["depletion_age"]}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
