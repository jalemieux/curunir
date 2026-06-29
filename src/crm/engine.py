"""CRM engine: all reads, writes, and rendering over the SQLite store.
Synchronous; importable for tests and for any harness.

Every public function takes the DB `path` as its first argument. Reads never
mutate; the only writers are add_lead / update_lead / set_stage / remove_lead /
log_interaction / import_rows.

Mirrors src/portfolio/engine.py. Zero agent/harness imports."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime

from src.crm import db as cdb

# Pipeline stages, in order. The allowed set for `stage`. Mirrors portfolio's
# ASSET_CLASSES — a per-deployment override is deferrable.
STAGES = ["new", "contacted", "qualified", "trial", "won", "lost"]

# Documented interaction kinds. `stage_change` is auto-logged by set_stage.
# Kept permissive (not hard-validated) so a caller can record an ad-hoc kind.
INTERACTION_KINDS = ("email", "call", "note", "meeting", "stage_change")

_REQUIRED = ("name",)
_COLUMNS = ("id", "name", "email", "company", "source", "stage", "owner",
            "note", "created_at", "updated_at", "extra")
_UPDATABLE = {"name", "email", "company", "source", "stage", "owner", "note",
              "extra"}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s or "lead"


def _norm(text: str) -> str:
    """Normalize text for fuzzy duplicate detection."""
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dump_extra(extra):
    return json.dumps(extra) if isinstance(extra, (dict, list)) else extra


# --- writes -----------------------------------------------------------------


def add_lead(path: str, fields: dict) -> dict:
    """Insert one lead. Returns {id, warnings[]}. Raises ValueError on a
    missing required field, an unknown stage, or an exact-duplicate email.
    Warns (does not block) on a near-duplicate name."""
    missing = [k for k in _REQUIRED if fields.get(k) in (None, "")]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")

    stage = fields.get("stage") or "new"
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; valid: {STAGES}")

    warnings: list[str] = []
    email = fields.get("email") or None

    con = cdb.connect(path)
    try:
        # Hard-reject an exact (case-insensitive) email duplicate so two rows
        # can't describe the same person; UNIQUE(email) is the backstop.
        if email:
            dup = con.execute(
                "SELECT id FROM leads WHERE lower(email) = lower(?)",
                (email,)).fetchone()
            if dup is not None:
                raise ValueError(
                    f"a lead already exists with email {email!r} "
                    f"(id={dup['id']!r})")

        # Near-duplicate NAME warning (fuzzy), paralleling portfolio's dup-label
        # warning — surfaced, never blocked.
        target = _norm(fields["name"])
        if target:
            for row in con.execute("SELECT name FROM leads"):
                other = _norm(row["name"])
                if other and (other in target or target in other):
                    warnings.append(
                        f"a similar lead already exists: {row['name']!r} — "
                        f"confirm this isn't a duplicate")
                    break

        base = _slug(fields.get("id") or fields["name"] or email)
        new_id, i = base, 2
        existing = {r["id"] for r in con.execute("SELECT id FROM leads")}
        while new_id in existing:
            new_id, i = f"{base}-{i}", i + 1

        now = _now_iso()
        record = {
            "id": new_id, "name": fields["name"], "email": email,
            "company": fields.get("company"), "source": fields.get("source"),
            "stage": stage, "owner": fields.get("owner"),
            "note": fields.get("note"),
            "created_at": fields.get("created_at") or now,
            "updated_at": now, "extra": _dump_extra(fields.get("extra")),
        }
        try:
            con.execute(
                f"INSERT INTO leads ({','.join(_COLUMNS)}) "
                f"VALUES ({','.join('?' for _ in _COLUMNS)})",
                tuple(record[c] for c in _COLUMNS))
            con.commit()
        except sqlite3.IntegrityError:
            raise ValueError(
                f"a lead already exists with email {email!r}")
    finally:
        con.close()
    return {"id": new_id, "warnings": warnings}


def show(path: str, lead_id: str) -> dict:
    con = cdb.connect(path)
    try:
        row = con.execute("SELECT * FROM leads WHERE id = ?",
                          (lead_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        raise KeyError(f"no lead with id {lead_id!r}")
    return dict(row)


def update_lead(path: str, lead_id: str, fields: dict) -> dict:
    """Update whitelisted columns on one lead. Raises KeyError if absent.
    Stamps updated_at. Validates stage if present."""
    sets = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not sets:
        raise ValueError(f"no updatable fields in {list(fields)}")
    if "stage" in sets and sets["stage"] not in STAGES:
        raise ValueError(f"unknown stage {sets['stage']!r}; valid: {STAGES}")
    if "extra" in sets:
        sets["extra"] = _dump_extra(sets["extra"])
    sets["updated_at"] = _now_iso()
    con = cdb.connect(path)
    try:
        if con.execute("SELECT 1 FROM leads WHERE id = ?",
                       (lead_id,)).fetchone() is None:
            raise KeyError(f"no lead with id {lead_id!r}")
        assigns = ", ".join(f"{k} = ?" for k in sets)
        con.execute(f"UPDATE leads SET {assigns} WHERE id = ?",
                    (*sets.values(), lead_id))
        con.commit()
    finally:
        con.close()
    return show(path, lead_id)


def set_stage(path: str, lead_id: str, stage: str) -> dict:
    """Move a lead to a new pipeline stage and log a `stage_change`
    interaction. Validates `stage` against STAGES; raises KeyError if the lead
    is absent."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; valid: {STAGES}")
    current = show(path, lead_id)  # raises KeyError if absent
    prev = current.get("stage")
    update_lead(path, lead_id, {"stage": stage})
    log_interaction(path, {
        "lead_id": lead_id, "kind": "stage_change",
        "body": f"{prev} → {stage}",
    })
    return {"id": lead_id, "stage": stage, "previous_stage": prev}


def remove_lead(path: str, lead_id: str) -> dict:
    """Delete a lead. Its interactions survive (soft ref, no FK)."""
    con = cdb.connect(path)
    try:
        cur = con.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
        con.commit()
    finally:
        con.close()
    if cur.rowcount == 0:
        raise KeyError(f"no lead with id {lead_id!r}")
    return {"removed": lead_id}


_INTERACTION_COLUMNS = ("id", "lead_id", "kind", "body", "occurred_at",
                        "created_at")


def log_interaction(path: str, fields: dict) -> dict:
    """Append an interaction to the ledger. Requires lead_id + kind; defaults
    occurred_at to now. Append-only and soft-ref — survives lead deletion."""
    for k in ("lead_id", "kind"):
        if fields.get(k) in (None, ""):
            raise ValueError(f"missing required interaction field: {k}")
    now = _now_iso()
    con = cdb.connect(path)
    try:
        base = _slug(f"i-{fields['lead_id']}-{fields['kind']}")
        new_id, i = base, 2
        existing = {r["id"] for r in con.execute("SELECT id FROM interactions")}
        while new_id in existing:
            new_id, i = f"{base}-{i}", i + 1
        rec = {
            "id": new_id, "lead_id": fields["lead_id"], "kind": fields["kind"],
            "body": fields.get("body"),
            "occurred_at": fields.get("occurred_at") or now,
            "created_at": now,
        }
        con.execute(
            f"INSERT INTO interactions ({','.join(_INTERACTION_COLUMNS)}) "
            f"VALUES ({','.join('?' for _ in _INTERACTION_COLUMNS)})",
            tuple(rec[c] for c in _INTERACTION_COLUMNS))
        con.commit()
    finally:
        con.close()
    return {"id": new_id, "lead_id": fields["lead_id"], "kind": fields["kind"]}


def import_rows(path: str, rows: list[dict], source: str | None = None,
                owner: str | None = None) -> dict:
    """Bulk-insert mapped lead rows (e.g. a parsed beta-signup export). Stamps
    `source`/`owner` on each when not already set. Validates the whole batch
    before any insert (all-or-nothing on validation), then adds each lead."""
    seen_emails = set()
    for idx, row in enumerate(rows):
        miss = [k for k in _REQUIRED if row.get(k) in (None, "")]
        if miss:
            raise ValueError(
                f"row {idx} ({row.get('name', '?')}): missing {', '.join(miss)}")
        stage = row.get("stage") or "new"
        if stage not in STAGES:
            raise ValueError(
                f"row {idx} ({row.get('name', '?')}): unknown stage {stage!r}")
        email = (row.get("email") or "").lower()
        if email:
            if email in seen_emails:
                raise ValueError(
                    f"row {idx}: duplicate email {row['email']!r} within the batch")
            seen_emails.add(email)

    imported, warnings = 0, []
    for row in rows:
        rec = dict(row)
        if source and not rec.get("source"):
            rec["source"] = source
        if owner and not rec.get("owner"):
            rec["owner"] = owner
        res = add_lead(path, rec)
        warnings.extend(f"[{rec.get('name', '?')}] {w}" for w in res["warnings"])
        imported += 1
    return {"imported": imported, "source": source, "owner": owner,
            "warnings": warnings}


# --- reads ------------------------------------------------------------------


def list_leads(path: str, stage: str | None = None, source: str | None = None,
               owner: str | None = None) -> list[dict]:
    """All leads, optionally filtered by stage / source / owner. Newest-first."""
    sql = "SELECT * FROM leads"
    clauses, params = [], []
    if stage:
        clauses.append("stage = ?"); params.append(stage)
    if source:
        clauses.append("source = ?"); params.append(source)
    if owner:
        clauses.append("owner = ?"); params.append(owner)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC, id DESC"
    con = cdb.connect(path)
    try:
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def pipeline(path: str) -> dict:
    """Lead counts by stage, read from v_pipeline_by_stage. Returns a stable
    shape: every stage in STAGES (zero-filled) plus a total. The model/UI reads
    this; it never re-counts."""
    con = cdb.connect(path)
    try:
        counts = {r["stage"]: r["n"]
                  for r in con.execute("SELECT * FROM v_pipeline_by_stage")}
    finally:
        con.close()
    by_stage = {s: int(counts.get(s, 0)) for s in STAGES}
    # Preserve any rows in non-standard stages so a count is never silently lost.
    for s, n in counts.items():
        if s not in by_stage:
            by_stage[s] = int(n)
    return {"total": sum(by_stage.values()), "by_stage": by_stage}


def activity(path: str, lead_id: str | None = None, since: str | None = None,
             limit: int | None = None) -> list[dict]:
    """Interaction ledger, newest-first. Filter by lead_id and/or `since`
    (occurred_at >=); cap with `limit`."""
    sql = "SELECT * FROM interactions"
    clauses, params = [], []
    if lead_id:
        clauses.append("lead_id = ?"); params.append(lead_id)
    if since:
        clauses.append("occurred_at >= ?"); params.append(since)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY occurred_at DESC, created_at DESC, id DESC"
    if limit is not None:
        sql += " LIMIT ?"; params.append(int(limit))
    con = cdb.connect(path)
    try:
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def query(path: str, sql: str) -> list[dict]:
    """Run an arbitrary read-only SELECT. Opened mode=ro, and the statement
    must begin with SELECT/WITH so a single ATTACH/PRAGMA can't escape the
    read-only contract."""
    head = sql.lstrip().lstrip("(").upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        raise ValueError("query() accepts only read-only SELECT/WITH statements")
    con = cdb.connect(path, readonly=True)
    try:
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


# --- render -----------------------------------------------------------------


def render_markdown(path: str) -> str:
    """Return a markdown view of the CRM, regenerated from the DB on demand.
    A caller writes the returned string to a generated, read-only memory file;
    this function itself writes no file."""
    pipe = pipeline(path)
    lines = [
        "# CRM (generated — do not hand-edit; source of truth is crm.db)",
        "", "## Pipeline", "",
        f"- Total leads: {pipe['total']}",
    ]
    for stage in STAGES:
        lines.append(f"- {stage.capitalize()}: {pipe['by_stage'].get(stage, 0)}")
    lines += ["", "## Leads", "",
              "| name | company | email | stage | source | owner |",
              "|---|---|---|---|---|---|"]
    for lead in list_leads(path):
        lines.append(
            f"| {lead.get('name') or ''} | {lead.get('company') or ''} | "
            f"{lead.get('email') or ''} | {lead.get('stage') or ''} | "
            f"{lead.get('source') or ''} | {lead.get('owner') or ''} |")
    return "\n".join(lines) + "\n"
