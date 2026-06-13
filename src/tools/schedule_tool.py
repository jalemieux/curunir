# src/tools/schedule_tool.py
"""CRUD operations for scheduled tasks, backed by the SQLite schedule store
(`context/schedules.db`). Thin string-rendering surface over
`src.schedule_store.engine`; all validation (cron, duplicate id, skill
allowlist) lives in the engine."""

from src.config import AgentConfig
from src.schedule_store import db as sdb
from src.schedule_store import engine


def _db(config: AgentConfig) -> str:
    sdb.init_db(str(config.schedules_db))
    return str(config.schedules_db)


def exec_schedule(args: dict, config: AgentConfig) -> str:
    action = args.get("action")
    if not action:
        return "Error: missing 'action' field. Use: list, add, update, remove, toggle."

    match action:
        case "list":
            return _list(config)
        case "add":
            return _add(args, config)
        case "update":
            return _update(args, config)
        case "remove":
            return _remove(args, config)
        case "toggle":
            return _toggle(args, config)
        case _:
            return f"Error: unknown action '{action}'. Use: list, add, update, remove, toggle."


def _list(config: AgentConfig) -> str:
    tasks = engine.list_schedules(_db(config))
    if not tasks:
        return "No scheduled tasks."
    lines = []
    for t in tasks:
        status = "enabled" if t["enabled"] else "disabled"
        skill = f" (skill: {t['skill']})" if t.get("skill") else ""
        lines.append(f"- **{t['id']}** `{t['cron']}` [{status}]{skill}\n  {t['prompt']}")
    return "\n".join(lines)


def _add(args: dict, config: AgentConfig) -> str:
    task_id = args.get("id")
    cron = args.get("cron")
    prompt = args.get("prompt")

    if not task_id or not cron or not prompt:
        return "Error: missing required fields — 'add' needs 'id', 'cron', and 'prompt'."

    try:
        engine.create(
            _db(config),
            {"id": task_id, "cron": cron, "prompt": prompt, "skill": args.get("skill")},
            skill_allowlist=config.skill_allowlist,
        )
    except ValueError as e:
        return f"Error: {e}."
    return f"Task '{task_id}' added — scheduled at `{cron}`."


def _update(args: dict, config: AgentConfig) -> str:
    task_id = args.get("id")
    if not task_id:
        return "Error: 'update' requires 'id' field."

    fields = {k: args[k] for k in ("cron", "prompt", "skill", "enabled") if k in args}
    if "enabled" in fields:
        fields["enabled"] = bool(fields["enabled"])
    try:
        engine.update(_db(config), task_id, fields, skill_allowlist=config.skill_allowlist)
    except ValueError as e:
        return f"Error: {e}."
    return f"Task '{task_id}' updated."


def _remove(args: dict, config: AgentConfig) -> str:
    task_id = args.get("id")
    if not task_id:
        return "Error: 'remove' requires 'id' field."
    try:
        engine.delete(_db(config), task_id)
    except ValueError as e:
        return f"Error: {e}."
    return f"Task '{task_id}' removed."


def _toggle(args: dict, config: AgentConfig) -> str:
    task_id = args.get("id")
    if not task_id:
        return "Error: 'toggle' requires 'id' field."
    try:
        row = engine.toggle(_db(config), task_id)
    except ValueError as e:
        return f"Error: {e}."
    state = "enabled" if row["enabled"] else "disabled"
    return f"Task '{task_id}' {state}."
