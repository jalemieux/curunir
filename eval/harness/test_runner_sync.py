"""Zero-cost tests for the eval runner's WS frame handling.

Reproduces the bug that corrupted the last run — a heavy task desyncing every
task after it — using a fake WebSocket that replays the exact frame sequence the
real server emits. No model calls, no SUT, no token spend.

    python eval/harness/test_runner_sync.py
"""

import asyncio
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import eval.harness.runner as R  # noqa: E402  (R.G is the graders module)


class FakeWS:
    """Mimics the server's WS frames. `feed()` queues frames; `__anext__`
    blocks when the queue is empty (so asyncio.wait_for can time out, exactly
    like waiting on a still-running agent)."""

    def __init__(self):
        self._q: collections.deque[str] = collections.deque()
        self._have = asyncio.Event()
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def feed(self, *frames: dict) -> None:
        for f in frames:
            self._q.append(json.dumps(f))
        self._have.set()

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        while not self._q:
            self._have.clear()
            await self._have.wait()
        return self._q.popleft()


# Frame builders mirroring run.py emissions.
def reset_ack():
    return {"content": "", "final": True}


# NB: summaries use the REAL server format (run.py _summarize_tool_call):
# "LoadSkill <name>", "Bash <cmd>", "Attach <name>" — NOT "load_skill: <name>".
def tool_frame(summary):
    return {"content": "", "tool_calls": [summary], "final": False}


def delta(chunk):
    return {"content": chunk, "delta": True, "final": False}


def final(text, attachments=None, stats=None):
    return {"content": text, "attachments": attachments,
            "stats": stats or {"wall_elapsed_sec": 2, "iterations": 3,
                               "completion_tokens": 40}, "final": True}


def check(name, cond, detail=""):
    print(f"  {'OK  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail and not cond else ""))
    return cond


async def test_sync_across_heavy_then_light() -> bool:
    """THE regression: a heavy task (many tool calls + a PDF) must not bleed
    into the next, light task. Each run_one must capture its OWN turn."""
    ws = FakeWS()
    # HEAVY task turn: reset ack, several tool calls, deltas, a final with a PDF.
    ws.feed(
        reset_ack(),
        tool_frame("LoadSkill financial-analysis"),
        tool_frame("Bash python skills/yfinance/yfin.py multiples KO"),
        delta("KO forward P/E "), delta("is 22x."),
        final("KO forward P/E is 22x. See attached.",
              attachments=[{"name": "ko_analysis.pdf", "path": "workspace/ko_analysis.pdf"}]),
    )
    heavy = await R.run_one(ws, {"prompt": "valuation read on KO", "max_loops": 14})

    # LIGHT task turn immediately after — distinct content.
    ws.feed(
        reset_ack(),
        tool_frame("Bash python skills/yfinance/yfin.py profile V"),
        final("Visa is in the financial services / payments sector."),
    )
    light = await R.run_one(ws, {"prompt": "what sector is Visa in", "max_loops": 6})

    ok = True
    ok &= check("heavy captured its own final text",
                "22x" in heavy.final_text, heavy.final_text)
    ok &= check("heavy captured the PDF attachment",
                any("ko_analysis.pdf" in (a.get("name") or "") for a in heavy.attachments))
    ok &= check("heavy PDF surfaced as an attach action (F11-style grader)",
                any("ko_analysis.pdf" in a for a in heavy.actions))
    ok &= check("LIGHT task got ITS OWN answer, not heavy's leftovers",
                "Visa" in light.final_text and "sector" in light.final_text, light.final_text)
    ok &= check("light task did NOT inherit heavy's actions",
                not any("financial-analysis" in a for a in light.actions))
    ok &= check("no extra reset was sent mid-stream",
                sum(1 for s in ws.sent if s.get("command") == "reset") == 2,
                f"{sum(1 for s in ws.sent if s.get('command') == 'reset')} resets")

    # The grader-format bug: 'LoadSkill financial-analysis' must satisfy a spec
    # written as 'load_skill: financial-analysis' (routing graders F1/F2/R7).
    st, why = R.G.action_used(heavy, {"require": ["load_skill: financial-analysis"]})
    ok &= check("routing grader matches normalized LoadSkill action", st == R.G.PASS, why)
    ok &= check("yfin.py substring still matches in a Bash action",
                R.G.action_used(heavy, {"require_any": ["yfin.py"]})[0] == R.G.PASS)
    return ok


async def test_multi_turn_readback() -> bool:
    """A `prompts:[...]` task runs as ONE multi-turn session: history is reset
    once, each prompt is sent in turn, and the graded Result reflects the FINAL
    reply while tool calls accumulate across the whole exchange (the W family)."""
    ws = FakeWS()
    ws.feed(
        reset_ack(),
        # turn 1: the write
        tool_frame("Edit context/memory/portfolios.md"),
        final("Added the Rolex Submariner ($9,200 basis, 2023-04-10)."),
        # turn 2: the readback — this is what the grader scores
        tool_frame("Read context/memory/portfolios.md"),
        delta("Cost basis "), delta("is $9,200; "),
        final("Cost basis is $9,200; acquired 2023-04-10, so it's long-term (~3 years)."),
    )
    res = await R.run_one(ws, {"prompts": [
        "Add a watch: Rolex Submariner, paid $9,200 on 2023-04-10, now worth $12,569.",
        "What's the cost basis and holding period on that Submariner?",
    ], "max_loops": 8})

    ok = True
    ok &= check("graded final_text is the READBACK reply, not the write",
                "long-term" in res.final_text and "$9,200" in res.final_text, res.final_text)
    ok &= check("the write turn's reply did NOT leak into final_text",
                "Added the Rolex" not in res.final_text, res.final_text)
    ok &= check("actions accumulate across BOTH turns",
                any("edit" in a for a in res.actions) and any("read" in a for a in res.actions),
                str(res.actions))
    ok &= check("history reset exactly once for the whole exchange",
                sum(1 for s in ws.sent if s.get("command") == "reset") == 1,
                f"{sum(1 for s in ws.sent if s.get('command') == 'reset')} resets")
    ok &= check("both prompts were sent",
                sum(1 for s in ws.sent if s.get("content", None) not in ("", None)) == 2,
                str([s.get("content") for s in ws.sent]))

    # The W1 grader runs on the readback: basis $9,200 + long-term holding.
    st, why = R.G.numeric_tolerance(res, {"expected": 9200, "tolerance_pct": 1})
    ok &= check("W1 numeric_tolerance finds the $9,200 basis in the readback", st == R.G.PASS, why)
    return ok


async def test_reconciles_grader() -> bool:
    """The new `reconciles` grader: a balance sheet must internally add up and
    match the anchored truth; a vague or drifting answer must fail."""
    def r(t):
        return R.G.Result(final_text=t)

    ok = True
    good = ("Total assets: $5,690,169. Total liabilities: $1,555,000. "
            "Net worth: $4,135,169.")
    st, why = R.G.reconciles(r(good), {"expected": 4135169})
    ok &= check("clean reconciling balance sheet passes", st == R.G.PASS, why)

    drift = ("Total assets $1,199,100, total liabilities $1,195,000, "
             "net worth $4,021,540.")  # the baseline-style drift
    st, why = R.G.reconciles(r(drift), {"expected": 4135169})
    ok &= check("non-reconciling answer fails", st == R.G.FAIL, why)

    st, why = R.G.reconciles(r("Your net worth is roughly $4 million."), {"expected": 4135169})
    ok &= check("vague answer with no balance sheet fails", st == R.G.FAIL, why)
    return ok


async def test_anchor_equals_and_cleanup() -> bool:
    """`anchor_equals` grades the STORE value (text-blind), and the runner's
    per-task cleanup runs commands best-effort after grading (K1's mechanics)."""
    ok = True
    r = R.G.Result(final_text="anything — this grader must ignore the text")
    st, why = R.G.anchor_equals(r, {"expected": "0 7 * * *", "equals": "0 7 * * *"})
    ok &= check("store value matching `equals` passes", st == R.G.PASS, why)
    st, why = R.G.anchor_equals(r, {"expected": "", "equals": "0 7 * * *"})
    ok &= check("absent row (empty store value) fails", st == R.G.FAIL, why)
    st, why = R.G.anchor_equals(r, {"equals": "0 7 * * *", "_anchor_error": "boom"})
    ok &= check("unresolved anchor is ERROR, not FAIL", st == R.G.ERROR, why)

    # Cleanup: the command runs (repo-root cwd), and a broken one is non-fatal.
    import tempfile
    marker = Path(tempfile.gettempdir()) / "curunir_eval_cleanup_marker"
    marker.unlink(missing_ok=True)
    R._run_cleanup({"cleanup": [
        [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('x')"],
        ["definitely-not-a-command-zzz"],  # must not raise
    ]})
    ok &= check("cleanup command executed", marker.exists())
    marker.unlink(missing_ok=True)
    ok &= check("task without cleanup is a no-op", R._run_cleanup({}) is None)
    return ok


async def test_timeout_interrupts_and_resyncs() -> bool:
    """On timeout the runner must interrupt out-of-band and still drain the one
    interrupted final — keeping the stream synced for the next task."""
    orig = R._task_timeout
    R._task_timeout = lambda task: 0.2  # force the timeout path fast
    try:
        ws = FakeWS()
        ws.feed(reset_ack(),
                tool_frame("Bash long running"),
                delta("working"))  # NO final → will time out

        async def feed_interrupted_final_later():
            await asyncio.sleep(0.4)  # after the 0.2s timeout fires
            ws.feed(final("(interrupted)"))

        asyncio.create_task(feed_interrupted_final_later())
        res = await R.run_one(ws, {"prompt": "slow task", "max_loops": 10})

        ok = True
        ok &= check("interrupt command was sent",
                    any(s.get("command") == "interrupt" for s in ws.sent))
        ok &= check("drained the interrupted final (stream resynced)",
                    "interrupted" in res.final_text, res.final_text)
        ok &= check("partial actions still captured",
                    any("long running" in a for a in res.actions))
        return ok
    finally:
        R._task_timeout = orig


async def test_socket_close_is_not_a_hang() -> bool:
    """If the socket closes without a final (server going away), the drain
    returns rather than hanging."""
    ws = FakeWS()
    ws.feed(reset_ack())  # reset ok

    # Exhaust then close: make __anext__ raise StopAsyncIteration after drain.
    class ClosingWS(FakeWS):
        async def __anext__(self):
            if not self._q:
                raise StopAsyncIteration
            return self._q.popleft()

    cw = ClosingWS()
    cw.feed(reset_ack())  # reset drain
    cw.feed(tool_frame("bash: x"))  # one frame then close, no final
    res = await asyncio.wait_for(R.run_one(cw, {"prompt": "p", "max_loops": 5}), 5)
    return check("closed socket yields a Result, no hang",
                 isinstance(res, R.G.Result))


async def main() -> None:
    print("test_sync_across_heavy_then_light:")
    a = await test_sync_across_heavy_then_light()
    print("test_multi_turn_readback:")
    b = await test_multi_turn_readback()
    print("test_reconciles_grader:")
    c = await test_reconciles_grader()
    print("test_anchor_equals_and_cleanup:")
    f = await test_anchor_equals_and_cleanup()
    print("test_timeout_interrupts_and_resyncs:")
    d = await test_timeout_interrupts_and_resyncs()
    print("test_socket_close_is_not_a_hang:")
    e = await test_socket_close_is_not_a_hang()
    results = [a, b, c, d, e, f]
    print(f"\n{'ALL PASS' if all(results) else 'FAILURES ABOVE'}")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
