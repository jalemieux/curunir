"""Schedule engine: all reads and writes over the SQLite schedule store.

Synchronous; importable for tests, the `schedule` tool, and the scheduler.
Every public function takes the DB `path` as its first argument. Editable-field
writes (create/update/delete/toggle) and run-metadata writes
(mark_attempt/mark_run) are scoped `UPDATE ... WHERE id=?` statements, so the
scheduler's metadata bookkeeping and a user edit never clobber each other —
SQLite handles the concurrency the JSON full-file rewrite could not.

Validation errors raise ValueError; the caller (tool/scheduler) surfaces them."""
from __future__ import annotations

import json
from pathlib import Path

from croniter import croniter

from src.schedule_store import db as sdb

# Columns returned by reads, in table order.
_COLUMNS = ("id", "cron", "skill", "prompt", "enabled", "last_run",
            "last_attempt_at", "last_status", "last_error")
# Fields a user may set via create/update.
_EDITABLE = ("cron", "skill", "prompt", "enabled")
_REQUIRED = ("id", "cron", "prompt")


def validate_cron(expr: str) -> bool:
    """True if `expr` is a valid croniter expression."""
    try:
        croniter(expr)
        return True
    except (ValueError, KeyError):
        return False


def _check_skill(skill, skill_allowlist) -> None:
    if skill and skill_allowlist is not None and skill not in skill_allowlist:
        raise ValueError(
            f"skill '{skill}' not allowed for this persona "
            f"(allowed: {sorted(skill_allowlist)})")


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["enabled"] = bool(d["enabled"])
    return d


def load(path: str) -> list[dict]:
    """All schedules as dicts, ordered by id. `enabled` coerced to bool;
    numeric run-metadata columns are never NULL (schema default 0)."""
    con = sdb.connect(path, readonly=True)
    try:
        rows = con.execute(
            f"SELECT {','.join(_COLUMNS)} FROM schedules ORDER BY id").fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        con.close()


# Alias mirroring the tool/scheduler vocabulary.
list_schedules = load


def _get(con, task_id: str):
    return con.execute(
        f"SELECT {','.join(_COLUMNS)} FROM schedules WHERE id=?",
        (task_id,)).fetchone()


def create(path: str, fields: dict, *, skill_allowlist=None) -> dict:
    """Insert one schedule. Raises ValueError on a missing required field,
    invalid cron, duplicate id, or a skill outside the allowlist."""
    missing = [k for k in _REQUIRED if fields.get(k) in (None, "")]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")
    if not validate_cron(fields["cron"]):
        raise ValueError(f"invalid cron expression '{fields['cron']}'")
    skill = fields.get("skill")
    _check_skill(skill, skill_allowlist)

    con = sdb.connect(path)
    try:
        if _get(con, fields["id"]) is not None:
            raise ValueError(f"task '{fields['id']}' already exists")
        enabled = 1 if fields.get("enabled", True) else 0
        con.execute(
            "INSERT INTO schedules (id, cron, skill, prompt, enabled) "
            "VALUES (?, ?, ?, ?, ?)",
            (fields["id"], fields["cron"], skill, fields["prompt"], enabled))
        con.commit()
        return _row_to_dict(_get(con, fields["id"]))
    finally:
        con.close()


def update(path: str, task_id: str, fields: dict, *, skill_allowlist=None) -> dict:
    """Update editable fields (cron/skill/prompt/enabled) only. Raises
    ValueError if the task is missing, the cron is invalid, or the skill is
    outside the allowlist. Run metadata is untouched."""
    if "cron" in fields and not validate_cron(fields["cron"]):
        raise ValueError(f"invalid cron expression '{fields['cron']}'")
    if "skill" in fields:
        _check_skill(fields["skill"], skill_allowlist)

    sets, params = [], []
    for key in _EDITABLE:
        if key in fields:
            val = fields[key]
            if key == "enabled":
                val = 1 if val else 0
            sets.append(f"{key}=?")
            params.append(val)

    con = sdb.connect(path)
    try:
        if _get(con, task_id) is None:
            raise ValueError(f"task '{task_id}' not found")
        if sets:
            params.append(task_id)
            con.execute(
                f"UPDATE schedules SET {','.join(sets)} WHERE id=?", params)
            con.commit()
        return _row_to_dict(_get(con, task_id))
    finally:
        con.close()


def delete(path: str, task_id: str) -> None:
    """Remove a schedule. Raises ValueError if it does not exist."""
    con = sdb.connect(path)
    try:
        cur = con.execute("DELETE FROM schedules WHERE id=?", (task_id,))
        con.commit()
        if cur.rowcount == 0:
            raise ValueError(f"task '{task_id}' not found")
    finally:
        con.close()


def toggle(path: str, task_id: str) -> dict:
    """Flip a schedule's enabled flag. Raises ValueError if it does not exist."""
    con = sdb.connect(path)
    try:
        row = _get(con, task_id)
        if row is None:
            raise ValueError(f"task '{task_id}' not found")
        con.execute("UPDATE schedules SET enabled=? WHERE id=?",
                    (0 if row["enabled"] else 1, task_id))
        con.commit()
        return _row_to_dict(_get(con, task_id))
    finally:
        con.close()


def mark_attempt(path: str, task_id: str, ts: int) -> None:
    """Stamp last_attempt_at before dispatch. Scoped to one column."""
    con = sdb.connect(path)
    try:
        con.execute("UPDATE schedules SET last_attempt_at=? WHERE id=?",
                    (ts, task_id))
        con.commit()
    finally:
        con.close()


def mark_run(path: str, task_id: str, ts: int, status: str,
             error: str | None = None) -> None:
    """Record a run outcome. On success the caller passes the completion ts;
    on error the caller passes the existing last_run (typically 0/unchanged) so
    last_run only advances on success."""
    con = sdb.connect(path)
    try:
        if status == "success":
            con.execute(
                "UPDATE schedules SET last_run=?, last_status=?, last_error=? "
                "WHERE id=?", (ts, status, error, task_id))
        else:
            con.execute(
                "UPDATE schedules SET last_status=?, last_error=? WHERE id=?",
                (status, error, task_id))
        con.commit()
    finally:
        con.close()


def migrate_from_json(path: str, json_path) -> int:
    """One-time import of legacy `schedules.json` into the table.

    Idempotent and gated: imports only when the table is empty and the JSON
    file exists. On a successful scan the source is renamed to
    `<name>.migrated` (even when empty) so it is not re-scanned. Returns the
    number of rows imported."""
    json_path = Path(json_path)
    if not json_path.exists():
        return 0
    if load(path):  # table already populated — never re-import
        return 0

    try:
        rows = json.loads(json_path.read_text())
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(rows, list):
        return 0

    con = sdb.connect(path)
    try:
        imported = 0
        for r in rows:
            if not isinstance(r, dict) or "id" not in r or "cron" not in r:
                continue
            con.execute(
                "INSERT OR IGNORE INTO schedules "
                "(id, cron, skill, prompt, enabled, last_run, last_attempt_at, "
                " last_status, last_error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r["id"], r["cron"], r.get("skill"), r.get("prompt"),
                    1 if r.get("enabled", True) else 0,
                    int(r.get("last_run", 0) or 0),
                    int(r.get("last_attempt_at", 0) or 0),
                    r.get("last_status"), r.get("last_error"),
                ))
            imported += 1
        con.commit()
    finally:
        con.close()

    json_path.rename(json_path.with_name(json_path.name + ".migrated"))
    return imported
