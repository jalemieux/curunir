"""Read adapters for the local web console.

Pure, independently-testable functions (graph nodes) that wrap the *existing*
read APIs so the co-located UI shows the same numbers the CLIs do and can't
drift:

- ``usage_summary``   → :meth:`UsageStore.summary` (same as ``python -m src.usage``)
- ``portfolio_overview`` → ``portfolio.engine`` (same as ``portfolio.py``)
- ``schedules``       → ``scheduler._load_tasks`` (+ read-only next-fire)
- ``memory_tree`` / ``memory_file`` → sandboxed walk/read of ``context/memory/``

Every function returns JSON-serializable dicts/lists. None of them mutate.
"""
from __future__ import annotations

import mimetypes
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from croniter import croniter

from src.agent import conversation_store
from src.config import AgentConfig
from src.crm import engine as crm_engine
from src.portfolio import engine
from src.scheduler import _load_tasks
from src.usage_store import UsageStore, normalize_session

# Mirror src.usage._parse_window so the UI and CLI accept identical windows.
_WINDOW_RE = re.compile(r"^(\d+)\s*([dhm])$")


def _parse_window(s: str) -> timedelta:
    m = _WINDOW_RE.match(s.strip().lower())
    if not m:
        raise ValueError(
            f"window must look like '7d', '24h', or '30m', got {s!r}"
        )
    n, unit = int(m.group(1)), m.group(2)
    return {
        "d": timedelta(days=n),
        "h": timedelta(hours=n),
        "m": timedelta(minutes=n),
    }[unit]


def usage_summary(
    config: AgentConfig, window: str = "7d", by: str = "model"
) -> dict:
    """Token/cost rollup over ``config.usage_db``.

    Returns ``{"window", "by", "rows", "available"}``. ``rows`` is exactly
    what :meth:`UsageStore.summary` returns. A missing DB yields empty rows
    and ``available=False`` rather than raising — a fresh deployment hasn't
    recorded any usage yet.
    """
    td = _parse_window(window)  # raises ValueError on a bad window
    db_path = Path(config.usage_db)
    if not db_path.exists():
        return {"window": window, "by": by, "rows": [], "available": False}
    store = UsageStore(db_path)
    try:
        rows = store.summary(td, group_by=by)
    finally:
        store.close()
    if by in ("session", "day_session"):
        rows = _label_sessions(config, rows)
    return {"window": window, "by": by, "rows": rows, "available": True}


# Fixed-session ids that have no conversation transcript get a friendly label.
_FIXED_SESSION_LABELS = {
    "cli": ("CLI", "cli"),
    "local": ("Local", "local"),
    "portal": ("Portal", "portal"),
}


def _session_label(session: str, convs: dict[str, dict]) -> tuple[str, str]:
    """Map a (normalized) session id to a ``(label, channel)`` for the UI.

    Scheduled jobs (``sched:<id>``) are labeled by job id; chat/email sessions
    borrow their conversation's title + channel; the fixed cli/local/portal
    slots get a friendly name; anything else shows a short id prefix.
    """
    if session.startswith("sched:"):
        return session.split(":", 1)[1], "sched"
    conv = convs.get(session)
    if conv and conv.get("title"):
        return conv["title"], conv.get("channel") or "web"
    if session in _FIXED_SESSION_LABELS:
        return _FIXED_SESSION_LABELS[session]
    return session[:8], "web"


def _firing_time(raw_session: str) -> str | None:
    """Local ``HH:MM`` for a scheduled run, parsed from ``sched:<id>:<epoch>``.

    Lets the By-day drill-down disambiguate a job that fired more than once in a
    day. Returns None for non-scheduled ids or an unparseable timestamp.
    """
    if not raw_session.startswith("sched:"):
        return None
    parts = raw_session.split(":")
    if len(parts) < 3:
        return None
    try:
        ts = int(parts[-1])
    except ValueError:
        return None
    return datetime.fromtimestamp(ts).astimezone().strftime("%H:%M")


def _label_sessions(config: AgentConfig, rows: list[dict]) -> list[dict]:
    """Annotate each session/day_session row with ``label`` + ``channel`` in place.

    Joins against the conversation store (read-only) so the breakdown shows real
    titles. ``by=session`` rows carry the collapsed key in ``session``;
    ``by=day_session`` rows carry the raw id in ``session_id`` (and a ``day``) —
    those are labeled by their normalized job/conversation id and get a ``detail``
    firing-time so repeated same-day runs stay distinct. A missing/unreadable
    store just yields fallback labels.
    """
    convs: dict[str, dict] = {}
    if getattr(config, "context_dir", None):
        convs = {
            c["session_id"]: c
            for c in conversation_store.list_conversations(config.context_dir)
        }
    for r in rows:
        raw = r.get("session_id") or r.get("session") or ""
        label, channel = _session_label(normalize_session(raw), convs)
        r["label"] = label
        r["channel"] = channel
        if "session_id" in r:  # day_session rows: disambiguate same-day reruns
            r["detail"] = _firing_time(raw)
    return rows


def portfolio_overview(config: AgentConfig, year: int | None = None) -> dict:
    """Balance-sheet snapshot from ``config.portfolio_db``.

    Returns ``{"available", "networth", "rollup", "assets", "collectibles_pnl",
    "unrealized", "trades", "realized", "as_of"}``. ``trades`` and ``realized``
    are scoped to ``year`` (default: current calendar year = fiscal year) so the
    trade ledger and the realized-P&L cards always cover the same window.
    ``as_of`` is the ``{oldest, newest}`` span of holdings' ``value_asof`` dates
    — the staleness caveat shown under the hero, since stored values aren't
    necessarily live.

    A missing store yields ``available=False`` with empty/None payloads — the
    engine's views don't exist on a fresh file, so we never touch it.
    """
    path = str(config.portfolio_db)
    if not os.path.exists(path):
        return {
            "available": False,
            "networth": None,
            "rollup": None,
            "assets": [],
            "collectibles_pnl": None,
            "unrealized": None,
            "trades": [],
            "realized": None,
            "as_of": None,
        }
    yr = year if year is not None else datetime.now().year
    assets = engine.list_assets(path)
    asof_dates = sorted(a["value_asof"] for a in assets if a.get("value_asof"))
    as_of = {"oldest": asof_dates[0], "newest": asof_dates[-1]} if asof_dates else None
    return {
        "available": True,
        "networth": engine.networth(path),
        "rollup": engine.rollup(path),
        "assets": assets,
        "collectibles_pnl": engine.pnl(path, cls="collectible"),
        "unrealized": engine.unrealized(path),
        "trades": engine.trade_history(path, since=f"{yr}-01-01"),
        "realized": engine.realized_pnl(path, year=yr),
        "as_of": as_of,
        "year": yr,
    }


def crm_overview(config: AgentConfig, activity_limit: int = 25) -> dict:
    """Mini-CRM snapshot from ``config.crm_db``.

    Returns ``{"available", "pipeline", "leads", "activity"}`` straight from
    ``crm.engine`` (``pipeline`` / ``list_leads`` / ``activity``) — no new query
    logic, so the UI can't drift from the ``crm`` tool / CLI. ``activity`` is the
    most-recent ``activity_limit`` interactions across all leads.

    A missing store yields ``available=False`` with empty payloads — the
    engine's views don't exist on a fresh file, so we never touch it.
    """
    path = str(config.crm_db)
    if not os.path.exists(path):
        return {
            "available": False,
            "pipeline": None,
            "leads": [],
            "activity": [],
        }
    return {
        "available": True,
        "pipeline": crm_engine.pipeline(path),
        "leads": crm_engine.list_leads(path),
        "activity": crm_engine.activity(path, limit=activity_limit),
    }


def schedules(config: AgentConfig) -> list[dict]:
    """Scheduled tasks from ``context/schedules.db``.

    Reuses ``scheduler._load_tasks`` (the same loader the scheduler runs) and
    annotates each task with ``next_fire`` (ISO-8601, UTC) computed read-only
    via croniter. A malformed cron leaves ``next_fire=None``.
    """
    tasks = _load_tasks(config)
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    for task in tasks:
        t = dict(task)
        cron = t.get("cron")
        next_fire = None
        if cron:
            try:
                next_fire = croniter(cron, now).get_next(datetime).isoformat()
            except (ValueError, KeyError):
                next_fire = None
        t["next_fire"] = next_fire
        t.setdefault("enabled", True)
        out.append(t)
    return out


def _memory_root(config: AgentConfig) -> Path:
    return (Path(config.context_dir) / "memory").resolve()


# Directories under context/memory we never expose (binary stores, caches).
_TREE_SKIP = {"__pycache__"}
_TREE_SKIP_EXT = {".db", ".db-wal", ".db-shm"}


def memory_tree(config: AgentConfig) -> list[dict]:
    """Nested tree of ``context/memory/``.

    Each node is ``{"name", "type", "relpath"}``; directory nodes also carry
    ``children``. Entries are sorted dirs-first then alphabetically. Binary
    stores (``portfolio.db`` etc.) and ``__pycache__`` are omitted — this is a
    markdown browser, not a file manager.
    """
    root = _memory_root(config)
    if not root.is_dir():
        return []

    def walk(directory: Path) -> list[dict]:
        nodes: list[dict] = []
        for entry in sorted(
            directory.iterdir(),
            key=lambda p: (p.is_file(), p.name.lower()),
        ):
            if entry.name in _TREE_SKIP or entry.name.startswith("."):
                continue
            if entry.is_file() and entry.suffix in _TREE_SKIP_EXT:
                continue
            relpath = entry.relative_to(root).as_posix()
            if entry.is_dir():
                nodes.append({
                    "name": entry.name,
                    "type": "dir",
                    "relpath": relpath,
                    "children": walk(entry),
                })
            else:
                nodes.append({
                    "name": entry.name,
                    "type": "file",
                    "relpath": relpath,
                })
        return nodes

    return walk(root)


def memory_file(config: AgentConfig, relpath: str) -> dict:
    """Read one file under ``context/memory/`` and return its raw text.

    Path-traversal guarded: ``relpath`` must resolve to a real file *inside*
    ``context/memory/``. Absolute paths and any ``..`` that escapes the root
    raise ``ValueError``; a non-existent file raises ``FileNotFoundError``.
    Markdown is returned raw for client-side rendering.
    """
    if os.path.isabs(relpath):
        raise ValueError(f"absolute paths not allowed: {relpath!r}")
    root = _memory_root(config)
    target = (root / relpath).resolve()
    # The resolved path must stay within the sandbox root.
    if root != target and root not in target.parents:
        raise ValueError(f"path escapes memory sandbox: {relpath!r}")
    if not target.is_file():
        raise FileNotFoundError(f"no memory file at {relpath!r}")
    return {
        "relpath": Path(relpath).as_posix(),
        "content": target.read_text(encoding="utf-8", errors="replace"),
    }


def _generated_root(config: AgentConfig) -> Path:
    return (Path(config.context_dir) / "workspace" / "generated").resolve()


# Directories/files under the generated dir we never expose.
_GEN_SKIP = {"__pycache__"}


def generated_files(config: AgentConfig) -> list[dict]:
    """Flat, newest-first listing of ``context/workspace/generated/``.

    Each row is ``{"relpath", "name", "size", "mtime", "mime"}`` — a posix
    relpath (subdirs reachable), the basename, byte size, mtime (epoch seconds),
    and a best-effort MIME type via :func:`mimetypes.guess_type`. Dotfiles and
    ``__pycache__`` are skipped. Returns ``[]`` if the dir is absent — nothing
    has been generated yet. The rail groups by day client-side; this stays a
    flat list sorted newest-first by mtime.
    """
    root = _generated_root(config)
    if not root.is_dir():
        return []

    rows: list[dict] = []
    for entry in root.rglob("*"):
        if not entry.is_file():
            continue
        rel_parts = entry.relative_to(root).parts
        if any(part in _GEN_SKIP or part.startswith(".") for part in rel_parts):
            continue
        stat = entry.stat()
        mime, _ = mimetypes.guess_type(entry.name)
        rows.append({
            "relpath": entry.relative_to(root).as_posix(),
            "name": entry.name,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "mime": mime,
        })
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows


def generated_file_path(config: AgentConfig, relpath: str) -> Path:
    """Resolve a file under ``context/workspace/generated/`` for download.

    Path-traversal guarded (same shape as :func:`memory_file`): ``relpath``
    must resolve to a real file *inside* the generated sandbox. Absolute paths
    and any ``..`` (or symlink) that escapes the root raise ``ValueError``; a
    non-existent target raises ``FileNotFoundError``. Returns the resolved
    absolute :class:`Path` — the *route* builds the ``FileResponse``.
    """
    if os.path.isabs(relpath):
        raise ValueError(f"absolute paths not allowed: {relpath!r}")
    root = _generated_root(config)
    target = (root / relpath).resolve()
    # The resolved path must stay within the sandbox root (symlinks collapsed).
    if root != target and root not in target.parents:
        raise ValueError(f"path escapes generated sandbox: {relpath!r}")
    if not target.is_file():
        raise FileNotFoundError(f"no generated file at {relpath!r}")
    return target
