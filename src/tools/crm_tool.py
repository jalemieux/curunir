"""Opt-in `crm` tool — structured-args surface over src.crm.engine.

Unlocked by the `crm` skill (frontmatter `tools: crm`). One tool with an
`action` + `args`, so only a single entry joins the unlocked set. Returns a
JSON string (the dispatcher contract). Mirrors portfolio_tool.py."""
from __future__ import annotations

import json

from src.config import AgentConfig
from src.crm import db as cdb
from src.crm import engine

_DEFAULT_DB = "context/memory/crm.db"

_READ = {
    "list": lambda db, a: engine.list_leads(
        db, stage=a.get("stage"), source=a.get("source"), owner=a.get("owner")),
    "show": lambda db, a: engine.show(db, a["id"]),
    "pipeline": lambda db, a: engine.pipeline(db),
    "activity": lambda db, a: engine.activity(
        db, lead_id=a.get("lead_id") or a.get("id"),
        since=a.get("since"), limit=a.get("limit")),
    "query": lambda db, a: engine.query(db, a["sql"]),
    "render": lambda db, a: {"markdown": engine.render_markdown(db)},
}
_WRITE = {
    "add": lambda db, a: engine.add_lead(db, a),
    "set": lambda db, a: engine.update_lead(db, a["id"], a.get("fields", {})),
    "set_stage": lambda db, a: engine.set_stage(db, a["id"], a["stage"]),
    "rm": lambda db, a: engine.remove_lead(db, a["id"]),
    "log": lambda db, a: engine.log_interaction(db, a),
    "import_rows": lambda db, a: engine.import_rows(
        db, a["rows"], source=a.get("source"), owner=a.get("owner")),
}


def exec_crm(args: dict, config: AgentConfig) -> str:
    db = getattr(config, "crm_db", None) or _DEFAULT_DB
    action = args.get("action")
    payload = args.get("args") or {}
    handler = _READ.get(action) or _WRITE.get(action)
    if handler is None:
        return json.dumps({"error": f"unknown action {action!r}",
                           "hint": f"valid: {sorted({*_READ, *_WRITE})}"})
    try:
        cdb.init_db(db)
        return json.dumps(handler(db, payload), default=str)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": str(e), "hint": "check action args; see SKILL.md"})
