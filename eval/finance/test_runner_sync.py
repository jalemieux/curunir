"""Zero-cost tests for the eval runner's WS frame handling.

Reproduces the bug that corrupted the last run — a heavy task desyncing every
task after it — using a fake WebSocket that replays the exact frame sequence the
real server emits. No model calls, no SUT, no token spend.

    python eval/finance/test_runner_sync.py
"""

import asyncio
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import eval.finance.run_finance_evals as R  # noqa: E402


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
    print("test_timeout_interrupts_and_resyncs:")
    b = await test_timeout_interrupts_and_resyncs()
    print("test_socket_close_is_not_a_hang:")
    c = await test_socket_close_is_not_a_hang()
    print(f"\n{'ALL PASS' if a and b and c else 'FAILURES ABOVE'}")
    sys.exit(0 if (a and b and c) else 1)


if __name__ == "__main__":
    asyncio.run(main())
