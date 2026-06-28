"""Time-injection tactic comparison harness.

Unlike the graded persona suites (eval/finance, eval/default) this does NOT
grade one agent — it A/B/C/D-compares *approaches* to telling the model the
current time, on the dimension that actually matters for auto-cache providers
(the configured MODEL, e.g. openrouter/z-ai/glm-5.2): does the tactic keep the
cacheable prefix byte-stable so prompt-cache reads stay high, while still giving
the model a fresh clock?

The four tactics (selected at SUT boot via CURUNIR_TIME_TACTIC):

    boot         frozen "now" baked into the prefix      cache-stable, STALE   (= main today)
    prefix_live  live "now" in the prefix, every turn    REWRITES PREFIX -> cache miss
    trailing     live "now" appended after history       cache-stable, fresh   (= PR #432)
    user_inline  live "now" folded into the user turn    cache-stable, fresh, persists

Method: for each tactic we boot a fresh SUT with that env, drive ONE fixed
multi-turn conversation (a couple of plain turns to grow history, one tool-loop
turn, then two time-probe turns), and read the per-turn stats frame the agent
already emits (prompt_tokens / cached_prompt_tokens). The discriminator is the
mean cached-% over turns 2..N: a tactic that rewrites the prefix can't reuse the
cache, so its cached-% collapses while the others stay high. The time probe is a
same-session sanity gate (does the reply carry today's date) — `boot`'s real
failure is on *resume*, a separate process, which #431's own tests cover.

Run (from repo root, with .venv active and .env populated):

    python eval/time_awareness/compare.py                       # all four tactics
    python eval/time_awareness/compare.py --tactics trailing prefix_live
    python eval/time_awareness/compare.py --keep-running        # reuse an already-running SUT for ONE tactic

Each tactic boot costs a real model round-trip per turn against the live
provider, so this is a handful of LLM calls per tactic — quick, but not free.
"""

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import websockets

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from eval.harness.runner import _drain_until_final, load_pairing_token  # noqa: E402

TACTICS = ["boot", "prefix_live", "trailing", "user_inline"]

# One fixed conversation, sent over a single session. Turn 1 seeds history;
# turns 2-3 grow it (turn 3 forces a tool loop so within-turn cache reuse is
# also exercised); turns 4-5 probe whether the model can read the current time.
# `probe_date` turns are checked against the SUT's real clock.
SCRIPT = [
    {"prompt": "Reply with exactly one word: ready.", "kind": "seed"},
    {"prompt": "List exactly three primary colors, one per line, nothing else.", "kind": "grow"},
    {"prompt": "Use the bash tool to run `echo CACHE_PROBE`, then tell me the exact text it printed.", "kind": "tool"},
    {"prompt": "What is today's date and the day of the week? Answer ONLY in the form YYYY-MM-DD (Weekday).", "kind": "probe_date"},
    {"prompt": "Based on the current time, is it AM or PM right now? Reply with one word: AM or PM.", "kind": "probe_ampm"},
]

WS_HOST, WS_PORT = "127.0.0.1", 8765


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


async def _wait_ws(timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_open(WS_HOST, WS_PORT):
            return True
        await asyncio.sleep(0.5)
    return False


def boot_sut(tactic: str, log_dir: Path) -> subprocess.Popen:
    """Launch `python run.py` with CURUNIR_TIME_TACTIC=<tactic>."""
    env = dict(os.environ)
    env["CURUNIR_TIME_TACTIC"] = tactic
    env.setdefault("LOG_LEVEL", "INFO")
    log_path = log_dir / f"sut-{tactic}.log"
    logf = open(log_path, "w")
    proc = subprocess.Popen(
        [sys.executable, "run.py"],
        cwd=str(REPO_ROOT), env=env, stdout=logf, stderr=subprocess.STDOUT,
    )
    proc._logf = logf  # type: ignore[attr-defined]
    proc._log_path = log_path  # type: ignore[attr-defined]
    return proc


def kill_sut(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    finally:
        getattr(proc, "_logf", None) and proc._logf.close()  # type: ignore[attr-defined]


async def drive(tactic: str, bloat_chars: int = 0) -> dict:
    """Drive the fixed SCRIPT over one session; capture per-turn stats."""
    uri = f"ws://{WS_HOST}:{WS_PORT}"
    turns = []
    async with websockets.connect(uri, max_size=None) as ws:
        hello = {"type": "hello"}
        token = load_pairing_token()
        if token is not None:
            hello["token"] = token
        await ws.send(json.dumps(hello))
        model = json.loads(await ws.recv()).get("model", "unknown")

        # Fresh session.
        await ws.send(json.dumps({"content": "", "command": "reset"}))
        await _drain_until_final(ws)

        script = list(SCRIPT)
        if bloat_chars:
            # Prepend a big filler turn so HISTORY (not just the static prefix)
            # carries real token weight. This is what separates the tactics:
            # `prefix_live` cannot cache any history after the moved timestamp,
            # so its uncached region grows with history; `trailing` keeps the
            # whole system+history prefix cacheable and only the trailing note
            # falls out. The model just acknowledges the blob.
            blob = ("REFERENCE-" * (bloat_chars // 10))
            script = [{"prompt": f"Here is reference material. Reply with only 'ok'.\n\n{blob}",
                       "kind": "bloat"}] + script

        for step in script:
            cap = {"actions": [], "deltas": [], "final_content": None,
                   "attachments": [], "stats": None}
            sent_at = datetime.now().astimezone()
            await ws.send(json.dumps({"content": step["prompt"], "command": None}))
            try:
                await asyncio.wait_for(_drain_until_final(ws, cap), 240)
            except asyncio.TimeoutError:
                await ws.send(json.dumps({"command": "interrupt"}))
                try:
                    await asyncio.wait_for(_drain_until_final(ws, cap), 60)
                except asyncio.TimeoutError:
                    pass
            stats = cap["stats"] or {}
            text = cap["final_content"] or "".join(cap["deltas"])
            prompt_tok = int(stats.get("prompt_tokens", 0))
            cached_tok = int(stats.get("cached_prompt_tokens", 0))
            turns.append({
                "kind": step["kind"],
                "prompt_tokens": prompt_tok,
                "cached_prompt_tokens": cached_tok,
                "cached_pct": round(100 * cached_tok / prompt_tok, 1) if prompt_tok else 0.0,
                "iterations": int(stats.get("iterations", 0)),
                "reply": text.strip()[:200],
                "sent_at": sent_at.isoformat(),
                "date_ok": _date_ok(step, text, sent_at),
            })
    return {"tactic": tactic, "model": model, "turns": turns}


def _date_ok(step: dict, text: str, sent_at: datetime):
    """Same-session sanity: probe replies should reflect the real clock."""
    if step["kind"] == "probe_date":
        return sent_at.strftime("%Y-%m-%d") in (text or "")
    if step["kind"] == "probe_ampm":
        want = "PM" if sent_at.hour >= 12 else "AM"
        return want.lower() in (text or "").lower()
    return None


def summarize(run: dict) -> dict:
    """Mean cached-% over turns 2..N (turn 1 is always a cold prefix)."""
    post = run["turns"][1:]
    cached = [t["cached_pct"] for t in post if t["prompt_tokens"]]
    probes = [t for t in run["turns"] if t["date_ok"] is not None]
    return {
        "tactic": run["tactic"],
        "mean_cached_pct": round(sum(cached) / len(cached), 1) if cached else 0.0,
        "per_turn_cached_pct": [t["cached_pct"] for t in run["turns"]],
        "time_correct": f"{sum(1 for p in probes if p['date_ok'])}/{len(probes)}",
    }


def print_table(summaries: list[dict]) -> None:
    print("\n" + "=" * 78)
    print("TIME-INJECTION TACTIC COMPARISON")
    print("=" * 78)
    print(f"{'tactic':<13} {'mean cache%':>11} {'per-turn cache%':>34} {'time ok':>9}")
    print("-" * 78)
    for s in summaries:
        per = " ".join(f"{p:>5.1f}" for p in s["per_turn_cached_pct"])
        print(f"{s['tactic']:<13} {s['mean_cached_pct']:>11} {per:>34} {s['time_correct']:>9}")
    print("-" * 78)
    print("mean cache% = mean cached_prompt_tokens/prompt_tokens over turns 2..N")
    print("(turn 1 is always cold). Low cache% = the tactic rewrites the prefix.")
    print("time ok = probe replies matching the SUT's real same-session clock.\n")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tactics", nargs="+", default=TACTICS, choices=TACTICS)
    ap.add_argument("--keep-running", action="store_true",
                    help="Drive an already-running SUT once (uses its current "
                         "CURUNIR_TIME_TACTIC); does not boot/kill.")
    ap.add_argument("--out", default=None, help="Write JSON results here.")
    ap.add_argument("--bloat-history", type=int, default=0, metavar="CHARS",
                    help="Prepend a filler turn of ~CHARS to load history with "
                         "real token weight (surfaces the prefix_live penalty "
                         "that a short conversation hides). Try 40000.")
    args = ap.parse_args()

    log_dir = REPO_ROOT / "eval" / "time_awareness" / "results"
    log_dir.mkdir(parents=True, exist_ok=True)
    runs = []

    if args.keep_running:
        if not _port_open(WS_HOST, WS_PORT):
            sys.exit(f"No SUT on {WS_HOST}:{WS_PORT}. Boot run.py first, or drop --keep-running.")
        tactic = os.environ.get("CURUNIR_TIME_TACTIC", "running-sut")
        print(f"Driving already-running SUT (tactic={tactic}) ...")
        runs.append(await drive(tactic, args.bloat_history))
    else:
        for tactic in args.tactics:
            if _port_open(WS_HOST, WS_PORT):
                sys.exit(f"Port {WS_PORT} already in use — stop the running SUT first "
                         f"(this harness boots its own per tactic).")
            print(f"\n### tactic={tactic}: booting SUT ...", flush=True)
            proc = boot_sut(tactic, log_dir)
            try:
                if not await _wait_ws():
                    print(f"  SUT did not come up; see {proc._log_path}")  # type: ignore[attr-defined]
                    continue
                print(f"  up. driving {len(SCRIPT) + (1 if args.bloat_history else 0)} turns ...", flush=True)
                runs.append(await drive(tactic, args.bloat_history))
            finally:
                kill_sut(proc)
                # Give the OS a moment to release the port before the next boot.
                for _ in range(20):
                    if not _port_open(WS_HOST, WS_PORT):
                        break
                    await asyncio.sleep(0.5)

    summaries = [summarize(r) for r in runs]
    print_table(summaries)

    out = Path(args.out) if args.out else log_dir / "compare.json"
    out.write_text(json.dumps({"runs": runs, "summaries": summaries}, indent=2))
    print(f"Full per-turn detail: {out}")


if __name__ == "__main__":
    asyncio.run(main())
