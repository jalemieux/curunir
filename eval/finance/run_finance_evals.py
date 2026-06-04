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
import html
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


async def _drain_until_final(ws, capture: dict | None = None, verbose: bool = False) -> bool:
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
                act = _normalize_action(tc if isinstance(tc, str) else str(tc))
                capture["actions"].append(act)
                if verbose:
                    print(f"    ├─ {act}", flush=True)
            text = data.get("content")
            if text:
                # The final frame carries the COMPLETE text; deltas carry chunks
                # of the same text. Keep the final as authoritative, deltas as
                # fallback, so we never double-count.
                if data.get("final"):
                    capture["final_content"] = text
                else:
                    capture["deltas"].append(text)
                if verbose and not data.get("final"):
                    print(text, end="", flush=True)
            for att in data.get("attachments") or []:
                capture["attachments"].append(att)
                if verbose:
                    print(f"\n    [attach] {att.get('name') or att.get('path')}", flush=True)
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


async def run_one(ws, task: dict, verbose: bool = False) -> G.Result:
    """Send one task's prompt, drain to its single final frame, build a Result."""
    # Clear history so tasks are independent; consume the reset's final ack.
    await ws.send(json.dumps({"content": "", "command": "reset"}))
    await _drain_until_final(ws)

    await ws.send(json.dumps({"content": task["prompt"], "command": None}))
    cap = {"actions": [], "deltas": [], "final_content": None,
           "attachments": [], "stats": None}

    timed_out = False
    try:
        await asyncio.wait_for(_drain_until_final(ws, cap, verbose), _task_timeout(task))
    except asyncio.TimeoutError:
        # Cancel out-of-band (interrupt is handled without enqueuing, so it adds
        # no extra final), then drain the one interrupted final to stay synced.
        timed_out = True
        await ws.send(json.dumps({"command": "interrupt"}))
        try:
            await asyncio.wait_for(_drain_until_final(ws, cap, verbose), 90)
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
        stats=stats,
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
            header = f"[{i}/{len(tasks)}] {task['id']} {task['name']}"
            # Verbose: header on its own line, then live tool calls / text below;
            # otherwise keep the compact single-line "header … STATUS why".
            print(header + ("" if args.verbose else " … "),
                  end="\n" if args.verbose else "", flush=True)
            result = await run_one(ws, task, verbose=args.verbose)
            if args.no_grade:
                status, why, checks = "—", "(not graded)", []
            else:
                status, why, checks = G.grade_detailed(task, result)
            prefix = "  => " if args.verbose else ""
            print(f"\n{prefix}{STATUS_MARK.get(status, status)} {why}" if args.verbose
                  else f"{STATUS_MARK.get(status, status)} {why}")
            rows.append({
                "id": task["id"], "name": task["name"], "tags": task.get("tags", []),
                "intent": task.get("intent"), "expected": task.get("expected"),
                "prompt": task["prompt"], "grader": task.get("grader"),
                "status": status, "why": why, "checks": checks,
                "actions": result.actions, "final_text": result.final_text,
                "attachments": [a.get("name") or a.get("path") for a in result.attachments],
                "wall_ms": result.wall_ms, "turns": result.turns,
                "tokens_out": result.tokens_out, "stats": result.stats,
                "error": result.error,
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

    # Markdown: lightweight summary table for quick GitHub viewing / diffing.
    # The verbose, interactive trace lives in the HTML report.
    md = [f"# Finance Eval Results: {model}", "",
          f"- Version: {version()}", f"- Timestamp: {payload['timestamp']}",
          f"- Summary: {summary}", "", "| id | name | status | why |",
          "|----|------|--------|-----|"]
    for r in rows:
        md.append(f"| {r['id']} | {r['name']} | {r['status']} | {r['why'].replace('|', '\\|')} |")
    (RESULTS_DIR / f"finance-{ts}-{safe}.md").write_text("\n".join(md) + "\n")

    # HTML: the primary human report — collapsible per-task trace, filters.
    html_path = RESULTS_DIR / f"finance-{ts}-{safe}.html"
    html_path.write_text(_html_report(payload, counts, summary))
    print(f"Report: {html_path}")
    print(f"  open {html_path}")


_STATUS_CLASS = {"pass": "pass", "fail": "fail", "pass-slow": "slow",
                 "error": "error", "—": "nograde"}

_CSS = """<style>
:root{--pass:#2e7d32;--fail:#c62828;--slow:#ef6c00;--error:#616161;--nograde:#9e9e9e;}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
 color:#1a1a1a;background:#f4f5f7}
header{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid #e0e0e0;
 padding:16px 24px;box-shadow:0 1px 4px rgba(0,0,0,.04)}
h1{margin:0 0 4px;font-size:18px}
.meta{color:#666;font-size:12px;margin-bottom:12px}
.meta code{background:#eef;padding:1px 5px;border-radius:4px}
.chips,.tags,.controls{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:8px}
.chip,.tag{cursor:pointer;border:1px solid #d0d0d0;background:#fafafa;border-radius:14px;
 padding:3px 11px;font-size:12px;color:#333}
.chip b{margin-left:3px}
.chip.active,.tag.active{border-color:#1a1a1a;background:#1a1a1a;color:#fff}
.chip.pass.active{background:var(--pass);border-color:var(--pass)}
.chip.fail.active{background:var(--fail);border-color:var(--fail)}
.chip.slow.active{background:var(--slow);border-color:var(--slow)}
.chip.error.active{background:var(--error);border-color:var(--error)}
#q{flex:1;min-width:180px;padding:5px 10px;border:1px solid #d0d0d0;border-radius:6px;font-size:13px}
.controls button{cursor:pointer;border:1px solid #d0d0d0;background:#fafafa;border-radius:6px;
 padding:5px 10px;font-size:12px}
main{max-width:1100px;margin:18px auto;padding:0 24px}
.card{background:#fff;border:1px solid #e3e3e3;border-left:4px solid var(--nograde);
 border-radius:8px;margin:10px 0;overflow:hidden}
.card.pass{border-left-color:var(--pass)}.card.fail{border-left-color:var(--fail)}
.card.slow{border-left-color:var(--slow)}.card.error{border-left-color:var(--error)}
summary{cursor:pointer;padding:11px 16px;display:flex;flex-wrap:wrap;gap:10px;align-items:center;
 list-style:none}
summary::-webkit-details-marker{display:none}
summary:hover{background:#fafafa}
.sid{font-weight:700;font-family:ui-monospace,Menlo,monospace;min-width:34px}
.badge{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
 padding:2px 8px;border-radius:10px;color:#fff;background:var(--nograde)}
.badge.pass{background:var(--pass)}.badge.fail{background:var(--fail)}
.badge.slow{background:var(--slow)}.badge.error{background:var(--error)}
.sname{font-weight:600}
.swhy{color:#555;flex:1;min-width:120px;font-size:13px}
.sstats{color:#999;font-size:11px;font-family:ui-monospace,Menlo,monospace}
.flag-empty{background:var(--fail);color:#fff;font-size:10px;font-weight:700;padding:1px 6px;border-radius:8px}
.body{padding:4px 16px 16px;border-top:1px solid #eee}
.sec{margin-top:14px}
h4{margin:0 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:#888}
.muted{color:#aaa;font-weight:400;text-transform:none;letter-spacing:0}
pre{margin:0;background:#f7f7f9;border:1px solid #ececf0;border-radius:6px;padding:10px 12px;
 white-space:pre-wrap;word-break:break-word;font:12px/1.45 ui-monospace,Menlo,monospace;
 max-height:460px;overflow:auto}
pre.prompt{background:#f0f4ff;border-color:#dde6fb}
.empty-banner{background:#fdecea;border:1px solid #f5c6c2;color:#9a1f17;border-radius:6px;
 padding:10px 12px;font-weight:600}
.empty-banner code{background:#fff;padding:1px 5px;border-radius:4px;font-weight:700}
ol.actions,ul.actions{margin:0;padding-left:22px}
ol.actions li,ul.actions li{margin:2px 0}
code{font-family:ui-monospace,Menlo,monospace;font-size:12px}
ul.checks{list-style:none;margin:0;padding:0}
ul.checks li{padding:3px 0;display:flex;gap:8px;align-items:baseline}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;flex:none;background:var(--nograde);
 position:relative;top:1px}
.dot.pass{background:var(--pass)}.dot.fail{background:var(--fail)}
.dot.slow{background:var(--slow)}.dot.error{background:var(--error)}
.gname{color:#aaa;font-size:11px;font-family:ui-monospace,Menlo,monospace}
.about{background:#fbfaf5;border:1px solid #ece6d3;border-radius:6px;padding:10px 14px}
.about p{margin:0 0 8px;font-size:13px;line-height:1.5}
.about p:last-child{margin-bottom:0}
.about .lbl{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
 color:#9a8c5c;font-weight:700;margin-bottom:1px}
.about .gradedby{color:#888;font-size:12px;border-top:1px dashed #e3dcc6;padding-top:7px}
table.stats{border-collapse:collapse;font-size:12px}
table.stats td{border:1px solid #eee;padding:3px 10px}
table.stats td:first-child{color:#888;font-family:ui-monospace,Menlo,monospace}
</style>"""

_JS = """<script>
const cards=[...document.querySelectorAll('.card')];
let statusFilter='all';const tagFilters=new Set();let q='';
function apply(){for(const c of cards){
 const okS=statusFilter==='all'||c.dataset.status===statusFilter;
 const ct=c.dataset.tags.split(' ');
 const okT=tagFilters.size===0||[...tagFilters].every(t=>ct.includes(t));
 const okQ=q===''||c.dataset.search.includes(q);
 c.style.display=(okS&&okT&&okQ)?'':'none';}}
document.querySelectorAll('[data-status-filter]').forEach(b=>b.onclick=()=>{
 statusFilter=b.dataset.statusFilter;
 document.querySelectorAll('[data-status-filter]').forEach(x=>x.classList.toggle('active',x===b));
 apply();});
document.querySelectorAll('[data-tag]').forEach(b=>b.onclick=()=>{
 const t=b.dataset.tag;
 if(tagFilters.has(t)){tagFilters.delete(t);b.classList.remove('active');}
 else{tagFilters.add(t);b.classList.add('active');}apply();});
document.getElementById('q').oninput=e=>{q=e.target.value.toLowerCase();apply();};
document.getElementById('expand').onclick=()=>cards.forEach(c=>c.open=true);
document.getElementById('collapse').onclick=()=>cards.forEach(c=>c.open=false);
</script>"""


def _esc(s) -> str:
    return html.escape("" if s is None else str(s))


def _card_html(r: dict) -> str:
    status = r.get("status", "—")
    cls = _STATUS_CLASS.get(status, "nograde")
    tags = " ".join(r.get("tags", []))
    ft = r.get("final_text") or ""
    is_empty = not ft.strip()
    search = _esc(" ".join([r.get("id", ""), r.get("name", ""), r.get("why", ""),
                            r.get("intent") or "", r.get("expected") or "",
                            r.get("prompt", ""), ft]).lower())

    flag = "<span class='flag-empty'>empty</span>" if is_empty else ""
    mini = f"{r.get('wall_ms', 0):.0f}ms · {r.get('turns', 0)}t · {r.get('tokens_out', 0)}tok"
    summary = (f"<summary><span class='sid'>{_esc(r.get('id'))}</span>"
               f"<span class='badge {cls}'>{_esc(status)}</span>"
               f"<span class='sname'>{_esc(r.get('name'))}</span>{flag}"
               f"<span class='swhy'>{_esc(r.get('why'))}</span>"
               f"<span class='sstats'>{_esc(mini)}</span></summary>")

    b = ["<div class='body'>"]

    intent, expected = r.get("intent"), r.get("expected")
    if intent or expected:
        b.append("<div class='sec'><div class='about'>")
        if intent:
            b.append(f"<p><span class='lbl'>What this tests</span>{_esc(intent)}</p>")
        if expected:
            b.append(f"<p><span class='lbl'>Expected behavior</span>{_esc(expected)}</p>")
        grader = r.get("grader")
        if grader:
            b.append(f"<p class='gradedby'>Graded by <code>{_esc(grader)}</code> — "
                     "the mechanical version of this contract is in <b>Grader checks</b> below.</p>")
        b.append("</div></div>")

    checks = r.get("checks") or []
    if checks:
        b.append("<div class='sec'><h4>Grader checks</h4><ul class='checks'>")
        for c in checks:
            ccls = _STATUS_CLASS.get(c.get("status"), "nograde")
            b.append(f"<li><span class='dot {ccls}'></span><b>{_esc(c.get('label'))}</b>"
                     f"<span class='gname'>{_esc(c.get('grader'))}</span>— {_esc(c.get('why'))}</li>")
        b.append("</ul></div>")

    b.append(f"<div class='sec'><h4>Prompt</h4><pre class='prompt'>{_esc(r.get('prompt'))}</pre></div>")

    if is_empty:
        err = r.get("error")
        note = f" Runner error: <code>{_esc(err)}</code>." if err else ""
        b.append("<div class='sec'><h4>Final text</h4><div class='empty-banner'>"
                 f"EMPTY RESPONSE — the agent returned no text.{note} It ran "
                 f"{r.get('turns', 0)} turn(s) and emitted {len(r.get('actions') or [])} "
                 "tool call(s) (see Actions).</div></div>")
    else:
        b.append(f"<div class='sec'><h4>Final text <span class='muted'>({len(ft)} chars)</span>"
                 f"</h4><pre class='final'>{_esc(ft)}</pre></div>")

    acts = r.get("actions") or []
    b.append(f"<div class='sec'><h4>Actions <span class='muted'>({len(acts)})</span></h4>")
    b.append("<ol class='actions'>" + "".join(f"<li><code>{_esc(a)}</code></li>" for a in acts)
             + "</ol>" if acts else "<div class='muted'>(no tool calls)</div>")
    b.append("</div>")

    att = r.get("attachments") or []
    if att:
        b.append("<div class='sec'><h4>Attachments</h4><ul class='actions'>"
                 + "".join(f"<li><code>{_esc(a)}</code></li>" for a in att) + "</ul></div>")

    st = r.get("stats") or {}
    keys = ["wall_elapsed_sec", "iterations", "llm_calls", "prompt_tokens",
            "completion_tokens", "total_tokens", "completion_tps", "llm_elapsed_sec"]
    srows = [f"<tr><td>{_esc(k)}</td><td>{_esc(st[k])}</td></tr>" for k in keys if k in st]
    if r.get("error"):
        srows.append(f"<tr><td>error</td><td>{_esc(r['error'])}</td></tr>")
    if srows:
        b.append("<div class='sec'><h4>Stats</h4><table class='stats'>" + "".join(srows) + "</table></div>")

    b.append("</div>")
    return (f"<details class='card {cls}' data-status='{_esc(status)}' "
            f"data-tags='{_esc(tags)}' data-search='{search}'>" + summary + "".join(b) + "</details>")


def _html_report(payload: dict, counts: dict, summary: str) -> str:
    rows = payload["results"]
    all_tags = sorted({t for r in rows for t in r.get("tags", [])})

    chips = [f"<button class='chip active' data-status-filter='all'>All <b>{len(rows)}</b></button>"]
    for key in ("pass", "fail", "pass-slow", "error"):
        if counts.get(key):
            chips.append(f"<button class='chip {_STATUS_CLASS[key]}' data-status-filter='{key}'>"
                         f"{key} <b>{counts[key]}</b></button>")
    tags = "".join(f"<button class='tag' data-tag='{_esc(t)}'>{_esc(t)}</button>" for t in all_tags)

    header = (
        "<header><h1>Finance Persona Evals</h1>"
        f"<div class='meta'>model <code>{_esc(payload['model'])}</code> · "
        f"version <code>{_esc(payload.get('version'))}</code> · {_esc(payload.get('timestamp'))}</div>"
        f"<div class='chips'>{''.join(chips)}</div>"
        f"<div class='tags'>{tags}</div>"
        "<div class='controls'><input id='q' placeholder='filter by id, name, reason, prompt, text…'>"
        "<button id='expand'>Expand all</button><button id='collapse'>Collapse all</button></div>"
        "</header>")

    cards = "".join(_card_html(r) for r in rows)
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>Finance Eval — {_esc(payload['model'])}</title>{_CSS}</head><body>"
            f"{header}<main id='list'>{cards}</main>{_JS}</body></html>")


def main() -> None:
    p = argparse.ArgumentParser(description="Run graded finance-persona evals")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--tag", help="run only tasks with a tag matching this regex")
    p.add_argument("--id", help="comma-separated task ids, e.g. R1,F3,C2")
    p.add_argument("--no-grade", action="store_true", help="capture only, skip grading")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="stream each task's tool calls and text live as it runs")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
