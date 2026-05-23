# src/tools/schedule_tool.py
"""CRUD operations for scheduled tasks stored in context/schedules.json.

Two layers:

* Structured helpers — ``list_tasks`` / ``add_task`` / ``update_task`` /
  ``remove_task`` — return dicts and ``(ok, error)`` tuples. These power
  both the LLM tool and the portal management UI.
* ``exec_schedule`` — thin LLM-facing wrapper that calls the helpers and
  formats the result as markdown. Its output shape is unchanged so the
  LLM tool keeps working identically.
"""

import json
import logging
import os
import tempfile
from pathlib import Path

from croniter import croniter

from src.config import AgentConfig

logger = logging.getLogger(__name__)

try:
    from cron_descriptor import ExpressionDescriptor  # type: ignore

    def _describe_cron(expr: str) -> str:
        try:
            return ExpressionDescriptor(expr, use_24hour_time_format=False).get_description()
        except Exception:
            return expr
except ImportError:  # pragma: no cover - dependency missing in some envs
    logger.warning("cron-descriptor not installed; cron_human will mirror raw expr")

    def _describe_cron(expr: str) -> str:
        return expr


def _schedule_path(config: AgentConfig) -> Path:
    return config.context_dir / "schedules.json"


def _load(config: AgentConfig) -> list[dict]:
    path = _schedule_path(config)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _save(config: AgentConfig, tasks: list[dict]) -> None:
    path = _schedule_path(config)
    # Atomic write: temp file + rename to prevent partial reads
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(tasks, f, indent=2)
        os.rename(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _validate_cron(expr: str) -> bool:
    try:
        croniter(expr)
        return True
    except (ValueError, KeyError):
        return False


def _next_run(expr: str) -> int | None:
    try:
        import time
        return int(croniter(expr, time.time()).get_next(float))
    except (ValueError, KeyError):
        return None


def _snapshot_row(task: dict) -> dict:
    """Normalize a stored task into the snapshot shape exposed over the wire."""
    cron = task.get("cron", "")
    return {
        "id": task.get("id"),
        "cron": cron,
        "cron_human": _describe_cron(cron) if cron else "",
        "prompt": task.get("prompt", ""),
        "skill": task.get("skill"),
        "enabled": bool(task.get("enabled", True)),
        "last_run": int(task.get("last_run", 0) or 0),
        "last_status": task.get("last_status"),
        "last_error": task.get("last_error"),
        "next_run": _next_run(cron) if cron else None,
    }


def list_tasks(config: AgentConfig) -> list[dict]:
    """Return all stored tasks as snapshot rows (cron_human, next_run, etc.)."""
    return [_snapshot_row(t) for t in _load(config)]


def add_task(config: AgentConfig, task: dict) -> tuple[bool, str | None]:
    """Append a new task. Returns (True, None) on success, (False, error) on
    validation or duplicate-id failure."""
    task_id = task.get("id")
    cron = task.get("cron")
    prompt = task.get("prompt")

    if not task_id or not cron or not prompt:
        return False, "missing required fields — 'id', 'cron', and 'prompt' are required."
    if not _validate_cron(cron):
        return False, f"invalid cron expression '{cron}'."

    tasks = _load(config)
    if any(t["id"] == task_id for t in tasks):
        return False, f"task '{task_id}' already exists."

    tasks.append({
        "id": task_id,
        "cron": cron,
        "prompt": prompt,
        "skill": task.get("skill"),
        "enabled": bool(task.get("enabled", True)),
        "last_run": 0,
    })
    _save(config, tasks)
    return True, None


def update_task(config: AgentConfig, task: dict) -> tuple[bool, str | None]:
    """Merge fields into an existing task identified by ``task['id']``."""
    task_id = task.get("id")
    if not task_id:
        return False, "'id' is required."

    tasks = _load(config)
    existing = next((t for t in tasks if t["id"] == task_id), None)
    if not existing:
        return False, f"task '{task_id}' not found."

    if "cron" in task:
        if not _validate_cron(task["cron"]):
            return False, f"invalid cron expression '{task['cron']}'."
        existing["cron"] = task["cron"]
    if "prompt" in task:
        existing["prompt"] = task["prompt"]
    if "skill" in task:
        existing["skill"] = task["skill"]
    if "enabled" in task:
        existing["enabled"] = bool(task["enabled"])

    _save(config, tasks)
    return True, None


def remove_task(config: AgentConfig, task_id: str) -> tuple[bool, str | None]:
    if not task_id:
        return False, "'id' is required."
    tasks = _load(config)
    new_tasks = [t for t in tasks if t["id"] != task_id]
    if len(new_tasks) == len(tasks):
        return False, f"task '{task_id}' not found."
    _save(config, new_tasks)
    return True, None


def task_exists(config: AgentConfig, task_id: str) -> bool:
    return any(t["id"] == task_id for t in _load(config))


def get_task(config: AgentConfig, task_id: str) -> dict | None:
    """Return the raw stored task (not the snapshot row) by id, or None."""
    for t in _load(config):
        if t["id"] == task_id:
            return t
    return None


def exec_schedule(args: dict, config: AgentConfig) -> str:
    action = args.get("action")
    if not action:
        return "Error: missing 'action' field. Use: list, add, update, remove."

    match action:
        case "list":
            return _format_list(config)
        case "add":
            ok, err = add_task(config, args)
            if not ok:
                return f"Error: {err}"
            return f"Task '{args['id']}' added — scheduled at `{args['cron']}`."
        case "update":
            ok, err = update_task(config, args)
            if not ok:
                return f"Error: {err}"
            return f"Task '{args['id']}' updated."
        case "remove":
            ok, err = remove_task(config, args.get("id", ""))
            if not ok:
                return f"Error: {err}"
            return f"Task '{args['id']}' removed."
        case _:
            return f"Error: unknown action '{action}'. Use: list, add, update, remove."


def _format_list(config: AgentConfig) -> str:
    tasks = _load(config)
    if not tasks:
        return "No scheduled tasks."
    lines = []
    for t in tasks:
        status = "enabled" if t.get("enabled", True) else "disabled"
        skill = f" (skill: {t['skill']})" if t.get("skill") else ""
        lines.append(f"- **{t['id']}** `{t['cron']}` [{status}]{skill}\n  {t['prompt']}")
    return "\n".join(lines)
