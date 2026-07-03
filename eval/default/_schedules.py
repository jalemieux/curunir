"""Anchor/cleanup helper for the K-family scheduling tripwire (K1).

Queries the SAME store the SUT's `schedule` tool writes (`context/schedules.db`,
via `src.schedule_store.engine`) so the grader and the agent can't drift apart —
the scheduling counterpart of `eval/finance/_networth.py`.

    python eval/default/_schedules.py show <id>     # {"exists", "cron", "prompt", "enabled"}
    python eval/default/_schedules.py delete <id>   # best-effort; {"deleted": <id>}

`show` always prints a JSON object (cron "" when the row is absent), so an
anchored `anchor_equals` check FAILs — rather than ERRORs — when the agent
never persisted the schedule (or persisted it under the wrong id).
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.schedule_store import engine  # noqa: E402

DB = str(REPO_ROOT / "context" / "schedules.db")


def main() -> None:
    verb, task_id = sys.argv[1], sys.argv[2]
    if verb == "show":
        try:
            rows = [t for t in engine.load(DB) if t.get("id") == task_id]
        except Exception:  # noqa: BLE001 — absent/locked db reads as "no row"
            rows = []
        row = rows[0] if rows else {}
        print(json.dumps({
            "exists": int(bool(rows)),
            "cron": row.get("cron", ""),
            "prompt": row.get("prompt", ""),
            "enabled": row.get("enabled", ""),
        }))
    elif verb == "delete":
        try:
            engine.delete(DB, task_id)
        except Exception:  # noqa: BLE001 — deleting an absent row is fine
            pass
        print(json.dumps({"deleted": task_id}))
    else:
        raise SystemExit(f"unknown verb {verb!r} (use: show|delete)")


if __name__ == "__main__":
    main()
