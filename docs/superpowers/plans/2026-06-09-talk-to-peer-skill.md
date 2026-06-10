# talk-to-peer Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a running curunir instance converse with another running instance "like a user would," by giving the agent a `talk-to-peer` skill plus a tiny WebSocket-client helper that messages peers configured in `CURUNIR_PEERS`.

**Architecture:** A standalone `skills/talk-to-peer/peer.py` speaks the existing inbound WS protocol (hello+token → content → read frames until `final`). The agent invokes it via the `bash` tool; the back-and-forth conversation loop is the agent's own reasoning loop. No core/channel/router changes. Peers and secrets live in one env var, `CURUNIR_PEERS` (JSON). A stable per-conversation `session_id` makes the peer remember the exchange across the per-message reconnects.

**Tech Stack:** Python 3.12+, asyncio, `websockets` 16.0, argparse; pytest + pytest-asyncio for tests.

**Spec:** `docs/superpowers/specs/2026-06-09-talk-to-peer-skill-design.md`

---

## File Structure

- **Create** `skills/talk-to-peer/peer.py` — the helper. Pure functions `parse_peers`, `peer_names`, `resolve_peer`; async `send_to_peer`; `main()` CLI with exit codes. Single focused file (~110 lines), matching the repo's one-file skill-helper pattern (`skills/balance-sheet/portfolio.py`, `skills/comfyui/comfy.py`).
- **Create** `skills/talk-to-peer/SKILL.md` — catalog entry + usage instructions.
- **Create** `tests/test_peer.py` — unit tests for parsing/listing and a fake-WS-server test for `send_to_peer`.
- **Modify** `.env.example` — document `CURUNIR_PEERS` and `CURUNIR_SELF_NAME`.
- **Modify** `README.md` and `docs/architecture.md` — list the new skill / add a changelog entry.

The helper keeps logic in importable pure functions so tests don't need a CLI harness; the only async surface (`send_to_peer`) is tested against a real loopback `websockets.serve` server.

---

### Task 1: Config parsing + peer listing (pure functions)

**Files:**
- Create: `skills/talk-to-peer/peer.py`
- Test: `tests/test_peer.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_peer.py`:

```python
# tests/test_peer.py
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
import websockets

# peer.py lives in a skill dir (not a package), so load it by path.
_PEER_PATH = Path(__file__).resolve().parent.parent / "skills" / "talk-to-peer" / "peer.py"
_spec = importlib.util.spec_from_file_location("peer", _PEER_PATH)
peer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(peer)


def test_parse_peers_empty():
    assert peer.parse_peers(None) == {}
    assert peer.parse_peers("   ") == {}


def test_parse_peers_valid():
    raw = '{"bob": {"url": "ws://b:8765", "token": "x"}}'
    peers = peer.parse_peers(raw)
    assert peers["bob"]["url"] == "ws://b:8765"
    assert peers["bob"]["token"] == "x"


def test_parse_peers_malformed_json():
    with pytest.raises(peer.PeerError, match="not valid JSON"):
        peer.parse_peers("{not json")


def test_parse_peers_wrong_top_shape():
    with pytest.raises(peer.PeerError, match="must be a JSON object"):
        peer.parse_peers('["bob"]')


def test_parse_peers_peer_missing_url():
    with pytest.raises(peer.PeerError, match="'url'"):
        peer.parse_peers('{"bob": {"token": "x"}}')


def test_peer_names_sorted():
    peers = peer.parse_peers('{"zed": {"url": "ws://z"}, "abe": {"url": "ws://a"}}')
    assert peer.peer_names(peers) == ["abe", "zed"]


def test_resolve_unknown_peer_lists_available():
    peers = peer.parse_peers('{"bob": {"url": "ws://b"}}')
    with pytest.raises(peer.PeerError, match="available: bob"):
        peer.resolve_peer(peers, "alice")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_peer.py -v`
Expected: FAIL — `FileNotFoundError`/import error because `skills/talk-to-peer/peer.py` does not exist yet.

- [ ] **Step 3: Create `peer.py` with the module header + pure functions**

Create `skills/talk-to-peer/peer.py`:

```python
#!/usr/bin/env python3
"""Talk to another curunir instance over its WebSocket channel.

Reads peer connection info from the CURUNIR_PEERS env var (JSON), connects to
a named peer as a WS client, sends one message, and prints the peer's final
reply. The agent invokes this via the bash tool; the conversational loop is the
agent's own reasoning loop. No server-side changes are required — the peer
just sees a normal user on its WebSocket channel.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import websockets
import websockets.exceptions

# Match the server's max_size so attachment-bearing frames aren't rejected.
_MAX_SIZE = 32 * 1024 * 1024
_DEFAULT_TIMEOUT = 120.0


class PeerError(Exception):
    """Raised for any user-facing failure (bad config, unknown peer, etc.)."""


def parse_peers(raw: str | None) -> dict:
    """Parse the CURUNIR_PEERS JSON value into a dict.

    Returns {} when unset/empty. Raises PeerError on malformed JSON or shape.
    """
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PeerError(f"CURUNIR_PEERS is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PeerError(
            "CURUNIR_PEERS must be a JSON object of name -> {url, token}"
        )
    for name, info in data.items():
        if not isinstance(info, dict) or "url" not in info:
            raise PeerError(
                f"peer {name!r} must be an object with at least a 'url'"
            )
    return data


def peer_names(peers: dict) -> list[str]:
    """Return sorted peer names (never urls/tokens)."""
    return sorted(peers)


def resolve_peer(peers: dict, name: str) -> dict:
    """Return connection info for *name*, or raise listing available names."""
    if name not in peers:
        available = ", ".join(peer_names(peers)) or "(none configured)"
        raise PeerError(f"unknown peer {name!r}; available: {available}")
    return peers[name]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_peer.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add skills/talk-to-peer/peer.py tests/test_peer.py
git commit -m "feat(skills): peer.py config parsing + peer listing"
```

---

### Task 2: `send_to_peer` over WebSocket (collect-until-final)

**Files:**
- Modify: `skills/talk-to-peer/peer.py`
- Test: `tests/test_peer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_peer.py`:

```python
@pytest.mark.asyncio
async def test_send_to_peer_streams_then_final():
    received = {}

    async def handler(ws):
        received["hello"] = json.loads(await ws.recv())
        received["msg"] = json.loads(await ws.recv())
        # Echo a hello (server normally does), then stream, then final.
        await ws.send(json.dumps(
            {"type": "hello", "session_id": received["hello"].get("session_id")}
        ))
        await ws.send(json.dumps({"delta": True, "content": "Hello "}))
        await ws.send(json.dumps({"delta": True, "content": "there"}))
        await ws.send(json.dumps({"final": True, "content": "Hello there"}))

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        reply = await peer.send_to_peer(
            url=f"ws://127.0.0.1:{port}",
            token="s3cret",
            session_id="peer:alice",
            message="hi",
            timeout=5,
        )

    assert reply == "Hello there"
    assert received["hello"]["token"] == "s3cret"
    assert received["hello"]["session_id"] == "peer:alice"
    assert received["msg"]["content"] == "hi"


@pytest.mark.asyncio
async def test_send_to_peer_non_streaming_uses_final_content():
    async def handler(ws):
        await ws.recv()
        await ws.recv()
        await ws.send(json.dumps({"final": True, "content": "whole reply"}))

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        reply = await peer.send_to_peer(
            f"ws://127.0.0.1:{port}", None, "peer:x", "hi", timeout=5
        )
    assert reply == "whole reply"


@pytest.mark.asyncio
async def test_send_to_peer_times_out_without_final():
    async def handler(ws):
        await ws.recv()
        await ws.recv()
        await ws.send(json.dumps({"delta": True, "content": "..."}))
        await asyncio.sleep(5)  # never sends final

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(peer.PeerError, match="no final reply"):
            await peer.send_to_peer(
                f"ws://127.0.0.1:{port}", None, "peer:x", "hi", timeout=0.3
            )


@pytest.mark.asyncio
async def test_send_to_peer_connection_refused():
    # Port 1 is not listening; connect should fail fast as a PeerError.
    with pytest.raises(peer.PeerError, match="failed"):
        await peer.send_to_peer(
            "ws://127.0.0.1:1", None, "peer:x", "hi", timeout=2
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_peer.py -k send_to_peer -v`
Expected: FAIL — `AttributeError: module 'peer' has no attribute 'send_to_peer'`.

- [ ] **Step 3: Implement `send_to_peer`**

Append to `skills/talk-to-peer/peer.py` (after `resolve_peer`):

```python
async def send_to_peer(
    url: str,
    token: str | None,
    session_id: str,
    message: str,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Connect to *url*, send *message*, return the peer's final reply text.

    Sends a hello frame (with *token* and a stable *session_id* so the peer
    keeps one continuing conversation across the per-message reconnects), then
    a content frame, then reads frames until one carries ``final: true``,
    accumulating streamed ``delta`` chunks. Falls back to the final frame's
    ``content`` for non-streaming servers. Raises PeerError on timeout,
    connection failure, or a close before any final frame.
    """
    async def _converse() -> str:
        async with websockets.connect(url, max_size=_MAX_SIZE) as ws:
            hello: dict = {"type": "hello", "session_id": session_id}
            if token:
                hello["token"] = token
            await ws.send(json.dumps(hello))
            await ws.send(json.dumps({"content": message}))

            parts: list[str] = []
            async for raw in ws:
                data = json.loads(raw)
                if data.get("type") == "hello":
                    continue
                if data.get("delta"):
                    parts.append(data.get("content") or "")
                    continue
                if data.get("final"):
                    text = "".join(parts).strip()
                    return text or (data.get("content") or "").strip()
            raise PeerError("connection closed before a final reply")

    try:
        return await asyncio.wait_for(_converse(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise PeerError(f"no final reply within {timeout:.0f}s") from exc
    except (OSError, websockets.exceptions.WebSocketException) as exc:
        raise PeerError(f"connection to {url} failed: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_peer.py -k send_to_peer -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add skills/talk-to-peer/peer.py tests/test_peer.py
git commit -m "feat(skills): peer.py WS send/collect-until-final"
```

---

### Task 3: CLI wiring (`main`, `--list`, `--peer`, session derivation, exit codes)

**Files:**
- Modify: `skills/talk-to-peer/peer.py`
- Test: `tests/test_peer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_peer.py`:

```python
def test_main_list_prints_names_no_secrets(monkeypatch, capsys):
    monkeypatch.setenv(
        "CURUNIR_PEERS",
        '{"bob": {"url": "ws://b:8765", "token": "sek"}, "amy": {"url": "ws://a"}}',
    )
    rc = peer.main(["--list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "amy" in out and "bob" in out
    assert "ws://" not in out      # never leak urls
    assert "sek" not in out        # never leak tokens


def test_main_list_empty(monkeypatch, capsys):
    monkeypatch.delenv("CURUNIR_PEERS", raising=False)
    rc = peer.main(["--list"])
    assert rc == 0
    assert "no peers configured" in capsys.readouterr().out


def test_main_unknown_peer_returns_error(monkeypatch, capsys):
    monkeypatch.setenv("CURUNIR_PEERS", '{"bob": {"url": "ws://b"}}')
    rc = peer.main(["--peer", "ghost", "hi"])
    assert rc == 1
    assert "unknown peer" in capsys.readouterr().err


def test_main_missing_message_returns_error(monkeypatch, capsys):
    monkeypatch.setenv("CURUNIR_PEERS", '{"bob": {"url": "ws://b"}}')
    rc = peer.main(["--peer", "bob"])
    assert rc == 1
    assert "usage" in capsys.readouterr().err


def test_main_derives_session_from_self_name(monkeypatch):
    monkeypatch.setenv("CURUNIR_PEERS", '{"bob": {"url": "ws://b", "token": "t"}}')
    captured = {}

    async def fake_send(*, url, token, session_id, message, timeout):
        captured.update(
            url=url, token=token, session_id=session_id,
            message=message, timeout=timeout,
        )
        return "ok"

    monkeypatch.setattr(peer, "send_to_peer", fake_send)
    rc = peer.main(["--peer", "bob", "hello peer", "--self-name", "alice"])
    assert rc == 0
    assert captured["session_id"] == "peer:alice"
    assert captured["url"] == "ws://b"
    assert captured["token"] == "t"
    assert captured["message"] == "hello peer"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_peer.py -k main -v`
Expected: FAIL — `AttributeError: module 'peer' has no attribute 'main'`.

- [ ] **Step 3: Implement `_build_parser`, `main`, and the entrypoint**

Append to `skills/talk-to-peer/peer.py`:

```python
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Talk to another curunir instance (a configured peer).",
    )
    p.add_argument(
        "--list", action="store_true",
        help="List configured peer names and exit.",
    )
    p.add_argument(
        "--peer", help="Name of the peer to message (from CURUNIR_PEERS).",
    )
    p.add_argument(
        "message", nargs="?", help="Message text to send to the peer.",
    )
    p.add_argument(
        "--session",
        help="Override the session id sent to the peer "
             "(default: peer:<self-name>).",
    )
    p.add_argument(
        "--self-name",
        default=os.environ.get("CURUNIR_SELF_NAME", "curunir"),
        help="This instance's label, used to derive the peer session id.",
    )
    p.add_argument(
        "--timeout", type=float, default=_DEFAULT_TIMEOUT,
        help="Seconds to wait for the peer's final reply.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        peers = parse_peers(os.environ.get("CURUNIR_PEERS"))
        if args.list:
            names = peer_names(peers)
            print("\n".join(names) if names else "(no peers configured)")
            return 0
        if not args.peer or not args.message:
            raise PeerError(
                'usage: peer.py --peer <name> "<message>"  (or --list)'
            )
        info = resolve_peer(peers, args.peer)
        session_id = args.session or f"peer:{args.self_name}"
        reply = asyncio.run(send_to_peer(
            url=info["url"],
            token=info.get("token"),
            session_id=session_id,
            message=args.message,
            timeout=args.timeout,
        ))
        print(reply)
        return 0
    except PeerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the full test file to verify it passes**

Run: `pytest tests/test_peer.py -v`
Expected: PASS (all tests — Tasks 1–3).

- [ ] **Step 5: Commit**

```bash
git add skills/talk-to-peer/peer.py tests/test_peer.py
git commit -m "feat(skills): peer.py CLI (--list/--peer, session derivation, exit codes)"
```

---

### Task 4: The `talk-to-peer` SKILL.md

**Files:**
- Create: `skills/talk-to-peer/SKILL.md`

- [ ] **Step 1: Write the skill file**

Create `skills/talk-to-peer/SKILL.md`:

```markdown
---
name: talk-to-peer
description: Use when the operator wants this curunir instance to message, consult, or hold a back-and-forth conversation with another running curunir instance (a configured "peer"). Reaches peers defined in the CURUNIR_PEERS env var over their WebSocket channel — the peer sees you as a normal user.
---

# Talking to a peer curunir instance

Another curunir instance can be reached over its WebSocket channel using the
helper at `skills/talk-to-peer/peer.py`. Peers and their secrets are configured
by the operator in the `CURUNIR_PEERS` environment variable, so you never need
to know URLs or tokens — refer to peers by name.

## See who is reachable

```bash
python skills/talk-to-peer/peer.py --list
```

Prints the configured peer names (one per line). If it prints
`(no peers configured)`, there is no peer to talk to — tell the operator to set
`CURUNIR_PEERS`.

## Send a message and read the reply

```bash
python skills/talk-to-peer/peer.py --peer <name> "your message here"
```

This sends the message to that peer and prints the peer's full reply to stdout.
The peer processes it exactly as it would a message from a human user.

## Holding a conversation (back-and-forth)

To converse turn-after-turn, just call the helper again with your next message.
The helper pins a stable session id, so the peer **remembers the conversation**
across calls — treat each invocation as one turn:

1. Send your opening message with `--peer <name> "..."`.
2. Read the peer's reply from the command output.
3. Decide your next message and call the helper again.
4. Repeat until the exchange reaches a natural end.

**Keep it bounded.** Decide up front roughly how many turns are useful and stop
when the goal is met or the conversation stops progressing — don't loop
indefinitely. Summarize the outcome for the operator when you finish.

## Options

- `--timeout <seconds>` — how long to wait for the peer's reply (default 120).
- `--session <id>` — override the session id (rarely needed).
- `--self-name <label>` — your own label; the peer session id defaults to
  `peer:<self-name>` (also set via the `CURUNIR_SELF_NAME` env var).

## When it fails

The helper exits non-zero and prints `error: ...` on stderr for an unknown
peer, malformed `CURUNIR_PEERS`, a refused connection, or a timeout. Report the
error to the operator rather than retrying blindly.
```

- [ ] **Step 2: Verify the skill is registered in the manifest**

Run: `python -c "from src.skills import build_skill_manifest; m = build_skill_manifest(); print('talk-to-peer' in m)"`
Expected: prints `True`.

(If `build_skill_manifest` requires arguments in this codebase, instead run
`grep -rl talk-to-peer skills/` to confirm the file exists and re-check the
manifest builder's signature in `src/skills.py`.)

- [ ] **Step 3: Commit**

```bash
git add skills/talk-to-peer/SKILL.md
git commit -m "feat(skills): add talk-to-peer SKILL.md"
```

---

### Task 5: Document config in `.env.example`, README, and architecture

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Add the env vars to `.env.example`**

Append to the end of `.env.example`:

```bash

# talk-to-peer skill — let this instance message another running curunir
# instance over its WebSocket channel. JSON map of peer name -> {url, token}.
# The token is the peer's pairing token (its context/.ws-token). Example:
#   CURUNIR_PEERS={"bob":{"url":"ws://bob-host:8765","token":"PEER_TOKEN"}}
# CURUNIR_PEERS=
# This instance's label; the peer-side conversation session id is peer:<label>.
# CURUNIR_SELF_NAME=curunir
```

- [ ] **Step 2: Add the skill to the README feature/skill list**

Find the skills list in `README.md`:

Run: `grep -n "balance-sheet\|email-send\|deep-research" README.md`

Add a line describing `talk-to-peer` alongside the other skills, matching the
surrounding format. Example line content:

```markdown
- **talk-to-peer** — converse with another running curunir instance over its WebSocket channel (peers configured in `CURUNIR_PEERS`).
```

- [ ] **Step 3: Add a changelog entry to `docs/architecture.md`**

Run: `grep -n "Changelog\|## Changelog" docs/architecture.md | tail -3`

Add an entry at the bottom changelog section:

```markdown
- **2026-06-09 — talk-to-peer skill.** Instance-to-instance conversation. A
  new `skills/talk-to-peer/peer.py` WS client lets the agent message peers
  configured in `CURUNIR_PEERS` (JSON map name -> {url, token}); the
  back-and-forth loop is the agent's own tool loop and the peer sees a normal
  user on its WebSocket channel. No core/channel changes.
```

- [ ] **Step 4: Run the full test suite to confirm nothing regressed**

Run: `pytest tests/test_peer.py -v && pytest tests/ -q`
Expected: `tests/test_peer.py` all PASS; full suite PASS (no regressions — this change adds files and docs only).

- [ ] **Step 5: Commit**

```bash
git add .env.example README.md docs/architecture.md
git commit -m "docs: document CURUNIR_PEERS and the talk-to-peer skill"
```

---

## Manual verification (after all tasks)

A real end-to-end check needs two instances. With one terminal per instance:

1. Start instance A normally (`python run.py`) — note its `context/.ws-token`.
2. Start instance B on a second port/working-copy with its own context dir
   (e.g. `WS_PORT=8766 python run.py`) — note B's `context/.ws-token`.
3. In A's environment set `CURUNIR_PEERS={"bee":{"url":"ws://127.0.0.1:8766","token":"<B_TOKEN>"}}`.
4. Connect to A with the CLI (`python cli.py`) and ask: *"Use the talk-to-peer
   skill to say hello to bee and tell me what it says."*
5. Confirm A lists `bee`, sends the message, and relays B's reply; ask a
   follow-up and confirm B remembers the prior turn (stable session id).

---

## Self-Review (completed by plan author)

- **Spec coverage:** `CURUNIR_PEERS` JSON config (Task 1, Task 5) ✓; `peer.py`
  `--list` names-only + `--peer` send/collect-until-final + timeout + session
  pinning (Tasks 1–3) ✓; SKILL.md with `--list`→`--peer`→multi-turn guidance
  (Task 4) ✓; error handling surfaced as non-zero exit + stderr (Task 3 tests)
  ✓; testing per spec — collect-until-final, `--list` no-secrets, error paths,
  session pinning (Tasks 1–3) ✓; docs (Task 5) ✓. The spec's "known
  considerations" (extraction-on-disconnect, symmetry, loop safety) are
  intentionally out of scope for v1 — loop safety is partly addressed by the
  SKILL.md "keep it bounded" guidance.
- **Placeholder scan:** no TBD/TODO; every code step shows complete code.
- **Type consistency:** `parse_peers`/`peer_names`/`resolve_peer`/`send_to_peer`/
  `main` signatures are identical across the tasks that define and call them;
  `send_to_peer` is always called with keyword args (`url`, `token`,
  `session_id`, `message`, `timeout`) in both `main` and the Task 3 fake.
```
