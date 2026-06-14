"""Opt-in `portfolio` tool — structured-args surface over src.portfolio.engine.

Unlocked by the `portfolio` skill (frontmatter `tools: portfolio`). One
tool with an `action` + `args`, so only a single entry joins the unlocked set.
Returns a JSON string (the dispatcher contract)."""
from __future__ import annotations

import json

from src.config import AgentConfig
from src.portfolio import db as pdb
from src.portfolio import engine

_DEFAULT_DB = "context/memory/portfolio.db"

_READ = {
    "networth": lambda db, a: engine.networth(db),
    "rollup": lambda db, a: engine.rollup(db),
    "list": lambda db, a: engine.list_assets(db, cls=a.get("class"), account=a.get("account")),
    "show": lambda db, a: engine.show(db, a["id"]),
    "re_equity": lambda db, a: engine.re_equity(db, a["property_id"]),
    "pnl": lambda db, a: engine.pnl(db, cls=a.get("class", "collectible")),
    "query": lambda db, a: engine.query(db, a["sql"]),
    "render": lambda db, a: {"markdown": engine.render_markdown(db)},
}
_WRITE = {
    "add": lambda db, a: engine.add_asset(db, a),
    "add_liability": lambda db, a: engine.add_liability(db, a),
    "set": lambda db, a: engine.update_asset(db, a["id"], a.get("fields", {})),
    "rm": lambda db, a: engine.remove_asset(db, a["id"]),
    "import_rows": lambda db, a: engine.import_rows(
        db, a["rows"], account=a.get("account"), stated_total=a.get("stated_total")),
    "refresh": lambda db, a: engine.refresh(db),
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
        return json.dumps({"error": str(e), "hint": "check action args; see SKILL.md"})
