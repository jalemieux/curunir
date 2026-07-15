"""Opt-in `portfolio` tool — structured-args surface over src.portfolio.engine.

Unlocked by the `balance-sheet` skill (frontmatter `tools: portfolio`). One
tool with an `action` + `args`, so only a single entry joins the unlocked set.
Returns a JSON string (the dispatcher contract)."""
from __future__ import annotations

import json

from src.config import AgentConfig
from src.portfolio import db as pdb
from src.portfolio import engine
from src.portfolio.brokers import service as broker_service
from src.portfolio.brokers.registry import enabled_adapters

_DEFAULT_DB = "context/memory/portfolio.db"


def _adapters():
    """Enabled brokerage adapters from the environment (config-only). Wrapped
    so tests can patch a fake set without touching the registry/env."""
    return enabled_adapters()

_READ = {
    "networth": lambda db, a: engine.networth(db),
    "rollup": lambda db, a: engine.rollup(db),
    "list": lambda db, a: engine.list_assets(db, cls=a.get("class"), account=a.get("account")),
    "show": lambda db, a: engine.show(db, a["id"]),
    "re_equity": lambda db, a: engine.re_equity(db, a["property_id"]),
    "pnl": lambda db, a: engine.pnl(db, cls=a.get("class", "collectible")),
    "query": lambda db, a: engine.query(db, a["sql"]),
    "render": lambda db, a: {"markdown": engine.render_markdown(db)},
    "trades": lambda db, a: engine.trade_history(
        db, ticker=a.get("ticker"), account=a.get("account"),
        side=a.get("side"), since=a.get("since")),
    "realized": lambda db, a: engine.realized_pnl(
        db, ticker=a.get("ticker"), account=a.get("account"), year=a.get("year")),
    "snapshots": lambda db, a: engine.list_snapshots(
        db, since=a.get("since"), until=a.get("until")),
    "list_snapshots": lambda db, a: engine.list_snapshots(
        db, since=a.get("since"), until=a.get("until")),
    "show_snapshot": lambda db, a: engine.show_snapshot(db, a.get("id") or "latest"),
    "diff_snapshots": lambda db, a: engine.diff_snapshots(db, a["a"], a["b"]),
    "snapshot_diff": lambda db, a: engine.diff_snapshots(db, a["a"], a["b"]),
    # Brokerage sync (read): normalized positions reconciled against the store.
    # A missing/unauthed adapter surfaces as available=False / needs_reauth in
    # the payload, never a raise.
    "broker_accounts": lambda db, a: broker_service.accounts(_adapters()),
    "broker_diff": lambda db, a: broker_service.diff(db, _adapters()),
}
_WRITE = {
    "add": lambda db, a: engine.add_asset(db, a),
    "add_liability": lambda db, a: engine.add_liability(db, a),
    "set": lambda db, a: engine.update_asset(db, a["id"], a.get("fields", {})),
    "rm": lambda db, a: engine.remove_asset(db, a["id"]),
    "import_rows": lambda db, a: engine.import_rows(
        db, a["rows"], account=a.get("account"), stated_total=a.get("stated_total")),
    "refresh": lambda db, a: engine.refresh(db, snapshot_before=bool(a.get("snapshot_before"))),
    "buy": lambda db, a: engine.record_buy(db, a),
    "sell": lambda db, a: engine.record_sell(db, a),
    "snapshot": lambda db, a: engine.snapshot(
        db, trigger=a.get("trigger", "manual"), note=a.get("note"),
        force=bool(a.get("force")), taken_at=a.get("taken_at")),
    # Brokerage sync (write): apply the conservative reconcile subset (re-price
    # matched holdings, insert new tickers; qty drift + missing-remote reported
    # only). Auth lives in the local web console's Balance Sheet tab.
    "broker_sync": lambda db, a: broker_service.sync(db, _adapters()),
}


def exec_portfolio(args: dict, config: AgentConfig) -> str:
    db = getattr(config, "portfolio_db", None) or _DEFAULT_DB
    action = args.get("action")
    payload = args.get("args") or {}
    handler = _READ.get(action) or _WRITE.get(action)
    if handler is None:
        return json.dumps({"error": f"unknown action {action!r}",
                           "hint": f"valid: {sorted({*_READ, *_WRITE})}"})
    try:
        pdb.init_db(db)
        return json.dumps(handler(db, payload), default=str)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": str(e), "hint": (
            "load the balance-sheet skill (/balance-sheet) or read its SKILL.md "
            "for correct action/args")})
