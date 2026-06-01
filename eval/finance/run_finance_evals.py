"""Graded eval runner for the finance persona.

Sends each task in `finance_tasks.TASKS` to a running Curunir instance over the
WebSocket channel, captures a `Result`, grades it, and prints a status table
plus a markdown/JSON report. Unlike `eval/run_evals.py` (capture-only, human
eyeballed), this scores each task pass/fail/pass-slow with a one-line reason.

Prereqs:
    CURUNIR_PERSONA=finance python run.py     # in one shell (the SUT)
    python eval/finance/run_finance_evals.py  # in another

Options:
    --host/--port      WS endpoint (default localhost:8765)
    --tag REGEX        run only tasks whose tags match (e.g. regression)
    --id  R1,F3        run only these task ids
    --no-grade         capture only, skip grading (like the legacy harness)

The judge grader (`llm_judge`) needs JUDGE_MODEL or MODEL + a key in the env
where THIS script runs.
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import websockets

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# Load .env so the llm_judge grader has an API key, same as run.py. The judge
# defaults to a Claude model (separate from the SUT — see finance_graders);
# set JUDGE_MODEL to override.
try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from eval.finance import finance_graders as G  # noqa: E402
from eval.finance.finance_tasks import TASKS  # noqa: E402

RESULTS_DIR = Path(__file__).parent / "results"


def load_pairing_token() -> str | None:
    """Pairing token for the WS channel: $CURUNIR_WS_TOKEN, else context/.ws-token.

    Mirrors cli.py — the server requires a `{"type":"hello","token":...}` first
    frame whenever a token is configured (context/.ws-token, mode 0600).
    """
    env = os.environ.get("CURUNIR_WS_TOKEN")
    if env:
        return env.strip() or None
    try:
        return (REPO_ROOT / "context" / ".ws-token").read_text().strip() or None
    except OSError:
        return None
STATUS_MARK = {G.PASS: "PASS ", G.FAIL: "FAIL ", G.PASS_SLOW: "SLOW ", G.ERROR: "ERR  "}


def _normalize_action(summary: str) -> str:
    """Canonicalize a streamed tool-call summary to `tool_name: arg`.

    The server streams display-formatted summaries (run.py _summarize_tool_call):
    `LoadSkill investment-memo`, `Bash python ...`, `Attach memo.pdf`, etc.
    Graders are written against a stable `load_skill: investment-memo` form, so
    we lower/snake-case the CamelCase tool head and re-attach the argument. The
    raw argument (paths, `yfin.py`, `.pdf`) is preserved for substring matching.
    """
    head, _, rest = summary.partition(" ")
    head = head.rstrip(":")
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", head).lower()
    return f"{snake}: {rest}".rstrip().rstrip(":").rstrip()


async def _drain_until_final(ws, capture: dict | None = None) -> bool:
    """Read frames until exactly ONE `final:true`, then return True.

    The agent emits exactly one final frame per turn (OutgoingMessage.final
    defaults True; deltas/tool-calls set it False). Consuming precisely that
    one terminal frame keeps the WS stream in sync across tasks — sending an
    extra reset mid-stream is what desynced the previous runner. When `capture`
    is given, accumulate tool calls, text deltas, the final content, any
    attachments, and stats into it.
    """
    async for raw in ws:
        data = json.loads(raw)
        if capture is not None:
            for tc in data.get("tool_calls") or []:
                capture["actions"].append(_normalize_action(tc if isinstance(tc, str) else str(tc)))
            text = data.get("content")
            if text:
                # The final frame carries the COMPLETE text; deltas carry chunks
                # of the same text. Keep the final as authoritative, deltas as
                # fallback, so we never double-count.
                if data.get("final"):
                    capture["final_content"] = text
                else:
                    capture["deltas"].append(text)
            for att in data.get("attachments") or []:
                capture["attachments"].append(att)
            if data.get("stats"):
                capture["stats"] = data["stats"]
        if data.get("final"):
            return True
    return False  # socket closed before a final — caller treats as error


def _task_timeout(task: dict) -> float:
    """Per-task wall budget for the drain. Scaled off max_loops, capped at 10m.

    This is a *timeout*, not the old truncating budget — on expiry we interrupt
    cleanly and still drain to the final, so the stream stays synced. Process
    budgets (PASS-SLOW) are graded separately from the captured wall/turns.
    """
    loops = task.get("max_loops") or 10
    return min(600.0, max(180.0, loops * 20.0))


async def run_one(ws, task: dict) -> G.Result:
    """Send one task's prompt, drain to its single final frame, build a Result."""
    # Clear history so tasks are independent; consume the reset's final ack.
    await ws.send(json.dumps({"content": "", "command": "reset"}))
    await _drain_until_final(ws)

    await ws.send(json.dumps({"content": task["prompt"], "command": None}))
    cap = {"actions": [], "deltas": [], "final_content": None,
           "attachments": [], "stats": None}

    timed_out = False
    try:
        await asyncio.wait_for(_drain_until_final(ws, cap), _task_timeout(task))
    except asyncio.TimeoutError:
        # Cancel out-of-band (interrupt is handled without enqueuing, so it adds
        # no extra final), then drain the one interrupted final to stay synced.
        timed_out = True
        await ws.send(json.dumps({"command": "interrupt"}))
        try:
            await asyncio.wait_for(_drain_until_final(ws, cap), 90)
        except asyncio.TimeoutError:
            pass

    final_text = cap["final_content"] or "".join(cap["deltas"])
    # Fold attachment markers into actions so PDF-checking graders catch them
    # even when the deliverable is a file rather than prose.
    for att in cap["attachments"]:
        name = att.get("name") or att.get("path") or "attachment"
        cap["actions"].append(f"attach: {name}")

    stats = cap["stats"] or {}
    return G.Result(
        final_text=final_text,
        actions=cap["actions"],
        attachments=cap["attachments"],
        wall_ms=float(stats.get("wall_elapsed_sec", 0.0)) * 1000.0,
        turns=int(stats.get("iterations", 0)),
        tokens_out=int(stats.get("completion_tokens", 0)),
        error="timeout" if timed_out and not final_text else None,
    )


def select(args) -> list[dict]:
    tasks = TASKS
    if args.id:
        wanted = {x.strip() for x in args.id.split(",")}
        tasks = [t for t in tasks if t["id"] in wanted]
    if args.tag:
        tasks = [t for t in tasks if any(re.search(args.tag, tag) for tag in t.get("tags", []))]
    return tasks


def version() -> str:
    try:
        return subprocess.check_output(["git", "describe", "--tags", "--always"], text=True).strip()
    except Exception:
        return "unknown"


async def main_async(args) -> None:
    tasks = select(args)
    if not tasks:
        print("No tasks matched the filter.")
        return

    uri = f"ws://{args.host}:{args.port}"
    print(f"Connecting to {uri} — {len(tasks)} task(s)\n")
    async with websockets.connect(uri, max_size=None) as ws:
        # First frame must be a hello carrying the pairing token, or the server
        # closes the socket with 1008 "auth" before the welcome.
        hello: dict = {"type": "hello"}
        token = load_pairing_token()
        if token is not None:
            hello["token"] = token
        await ws.send(json.dumps(hello))

        model = json.loads(await ws.recv()).get("model", "unknown")
        print(f"Model under test: {model}\n")

        rows = []
        for i, task in enumerate(tasks, 1):
            print(f"[{i}/{len(tasks)}] {task['id']} {task['name']} … ", end="", flush=True)
            result = await run_one(ws, task)
            if args.no_grade:
                status, why = "—", "(not graded)"
            else:
                status, why = G.grade(task, result)
            print(f"{STATUS_MARK.get(status, status)} {why}")
            rows.append({
                "id": task["id"], "name": task["name"], "tags": task.get("tags", []),
                "prompt": task["prompt"], "status": status, "why": why,
                "actions": result.actions, "final_text": result.final_text,
                "attachments": [a.get("name") or a.get("path") for a in result.attachments],
                "wall_ms": result.wall_ms, "turns": result.turns,
                "tokens_out": result.tokens_out, "error": result.error,
            })

    _report(model, rows)


def _report(model: str, rows: list[dict]) -> None:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = "  ".join(f"{STATUS_MARK.get(k, k).strip()}={v}" for k, v in sorted(counts.items()))
    print(f"\n{'='*64}\nSUMMARY  {summary}\n{'='*64}")

    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^\w.-]", "_", model)
    payload = {
        "version": version(), "model": model,
        "timestamp": datetime.now(timezone.utc).isoformat(), "results": rows,
    }
    (RESULTS_DIR / f"finance-{ts}-{safe}.json").write_text(json.dumps(payload, indent=2, default=str))

    md = [f"# Finance Eval Results: {model}", "",
          f"- Version: {version()}", f"- Timestamp: {payload['timestamp']}",
          f"- Summary: {summary}", "", "| id | name | status | why |",
          "|----|------|--------|-----|"]
    for r in rows:
        why = r["why"].replace("|", "\\|")
        md.append(f"| {r['id']} | {r['name']} | {r['status']} | {why} |")
    out = RESULTS_DIR / f"finance-{ts}-{safe}.md"
    out.write_text("\n".join(md) + "\n")
    print(f"Report: {out}")


def main() -> None:
    p = argparse.ArgumentParser(description="Run graded finance-persona evals")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--tag", help="run only tasks with a tag matching this regex")
    p.add_argument("--id", help="comma-separated task ids, e.g. R1,F3,C2")
    p.add_argument("--no-grade", action="store_true", help="capture only, skip grading")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
