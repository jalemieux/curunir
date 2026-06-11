# talk-to-peer skill — design

**Date:** 2026-06-09
**Status:** Approved design, pre-implementation
**Author:** brainstorm session

## Problem

We want one running curunir instance to talk to another *like a user would* —
an autonomous back-and-forth where instance A converses turn-after-turn with a
separate instance B (each in its own container), with the operator watching.

The instances are already independent deployments, each exposing the
WebSocket channel on its own published port. We do **not** want to modify the
core agent loop, add a new channel, or stand up an external orchestrator
process.

## Key facts (verified)

- The main container is **WebSocket-only**. There is no HTTP server in `src/`
  (no fastapi/aiohttp/uvicorn); the only inbound surface is
  `WebSocketChannel` on `:8765`. → plain `curl` is not a viable transport.
- The inbound WS protocol is small (see `src/channels/ws.py`):
  - client connects, sends `{"type":"hello","token":"<token>","session_id":"<optional>"}`
  - client sends `{"content":"<text>"}`
  - server streams `{"delta":true,"content":"…"}` chunks, then a terminal
    `{"final":true,"content":"…", …}` frame.
  - `cli.py` is just one client of this protocol.
- A hello frame that names a prior `session_id` **resumes** that session
  (`_rekey` in `ws.py`). The agent keys conversation history by `session_id`,
  so pinning a stable id keeps the peer's memory of the conversation across
  reconnects.
- Config is plain `load_dotenv()` + `os.environ.get(...)` in `run.py`, so peer
  config is just another env var.

## Approach (chosen)

Make peer-talk a **capability the agent has**: a `talk-to-peer` skill plus a
tiny WS-client helper script. The agent loads the skill, discovers configured
peers, and drives the conversation through its normal tool loop — each
outbound message + reply is one `bash` invocation of the helper, and the
"back-and-forth loop" is the agent's own reasoning loop. No external
orchestrator; discoverable like every other skill; the peer instance needs
nothing special (it just sees a normal user on its WS port).

```
.env (instance A)                          skills/talk-to-peer/
┌─────────────────────────┐               ┌──────────────────────┐
│ CURUNIR_PEERS={          │   reads env   │ SKILL.md  (catalog)  │
│   "bob": {               │◄──────────────│   how to reach a peer │
│     "url":"ws://b:8765",  │               │ peer.py   (helper)   │
│     "token":"s3cret"}}    │               └──────────┬───────────┘
└─────────────────────────┘                           │ bash tool
        agent loop in A                                 ▼
   ─────────────────────────      WS hello+token   ┌───────────┐
   load_skill talk-to-peer                          │ instance  │
   bash: peer.py --peer bob "hi" ──────────────────►│  B :8765  │
                          ◄── B's final reply ───────│           │
   (reason, decide next msg)                         └───────────┘
   bash: peer.py --peer bob "…"   ← the loop IS the agent's own loop
```

## Components

### 1. Config — `CURUNIR_PEERS` (env)

A JSON object mapping a short peer name to its connection info:

```
CURUNIR_PEERS={"bob":{"url":"ws://bob-host:8765","token":"s3cret"}}
```

- One env var; secrets live with the operator (same place as every other key).
- Parsed by the helper at runtime. Malformed JSON → the helper prints a clear
  error and exits non-zero (the agent sees it in the tool result).
- Documented in `.env.example`.

### 2. Helper — `skills/talk-to-peer/peer.py`

A standalone ~80-line async WS client (the protocol extracted from `cli.py`,
stripped of all terminal UI). Subcommands:

- `peer.py --list` — read `CURUNIR_PEERS`, print configured peer **names only**
  (never tokens/urls-with-secrets). This is how the agent learns who is
  reachable, so the static skill text never has to encode the live config.
- `peer.py --peer <name> "<message>"` — connect to that peer's `url`, send
  `{"type":"hello","token":<token>,"session_id":<pinned>}`, send
  `{"content":"<message>"}`, read frames until `{"final":true}`, accumulate
  `content`/`delta`, print the reply text to stdout, exit 0. On error
  (unknown peer, connection refused, timeout, auth close) print a clear
  message and exit non-zero.

Behavioral details:
- **Session pinning.** The hello uses a stable `session_id` derived from this
  instance's own identity (default `peer:<self-name>`, overridable with
  `--session`). This makes the peer treat the whole exchange as one
  continuing conversation rather than a fresh session per message.
  `--self-name` / a `CURUNIR_SELF_NAME` env var supplies the self label;
  falls back to a constant if unset.
- **Timeout.** A `--timeout` (default ~120s) bounds the wait for `final` so a
  stuck peer can't hang the agent's tool call.
- **No streaming UI.** Deltas are accumulated silently; only the final reply
  is printed.

### 3. Skill — `skills/talk-to-peer/SKILL.md`

Frontmatter + catalog body:
- `name: talk-to-peer`
- `description:` when to use it (the user wants this instance to consult /
  converse with another curunir instance / peer agent).
- Body instructs the agent to:
  1. run `python skills/talk-to-peer/peer.py --list` to see reachable peers;
  2. send a message with `--peer <name> "<text>"` and read the reply from the
     tool result;
  3. to hold a multi-turn exchange, simply call again with the next message —
     the peer remembers the conversation (pinned session);
  4. how to interpret/relay the peer's reply and when to stop.
- No opt-in `tools:` needed — the skill drives everything through the existing
  `bash` tool. (An opt-in `peer` tool à la `portfolio` is a heavier
  alternative, explicitly out of scope here.)

## Data flow (one turn)

```
A.agent ──bash──► peer.py --peer bob "msg"
                      │  ws.connect(bob.url)
                      │  send hello{token, session_id=peer:A}
                      │  send {content:"msg"}
                      │  recv delta* , recv final ──► stdout (reply)
                      ▼
              tool result back to A.agent ──► A reasons ──► next bash call
```

## Error handling

- Unknown peer name → non-zero exit + `available peers: …` hint.
- Malformed `CURUNIR_PEERS` → non-zero exit + parse error.
- Connection refused / DNS / auth-close (1008) → non-zero exit + reason.
- Timeout waiting for `final` → non-zero exit + "no final reply within Ns".
- All errors surface as the bash tool result, so the agent can decide whether
  to retry, pick another peer, or report back to the user.

## Testing

- `peer.py` collect-until-final: drive against a fake `websockets` server that
  emits a couple of `delta` frames then a `final` frame; assert the printed
  reply equals the concatenation. Mirrors the `tests/test_channels.py` style.
- `--list`: parse sample `CURUNIR_PEERS` JSON, assert names printed and tokens
  absent.
- Error paths: malformed JSON, unknown peer, connection refused, timeout.
- Session pinning: assert the hello frame carries the expected `session_id`.

## Known considerations / follow-ups (not blocking)

- **Memory-extraction on disconnect.** `ws.py` enqueues an `extract` command
  whenever a WS client disconnects. Because the helper connects per message,
  an N-turn conversation triggers N extraction passes on the peer — wasteful
  but not breaking. Possible future mitigation: an `ephemeral:true` hello flag
  that suppresses extraction for peer connections. Out of scope for v1; note
  it and revisit if it proves noisy.
- **Symmetry.** Any instance with the skill + a `CURUNIR_PEERS` entry can
  initiate; the initiator holds the loop. No extra work for symmetric setups.
- **Loop safety.** The operator-facing risk is an unbounded A↔B exchange. v1
  relies on the initiating agent's judgment + the operator watching;
  a hard turn cap could be added to the skill guidance if needed.

## Out of scope

- HTTP endpoint / native `curl` support (WS-only is sufficient; revisit only
  if a curl-native path becomes a hard requirement).
- A registered opt-in `peer` tool (the bash+skill route is lighter).
- Changes to the core agent loop, channels, or router.
- Long-lived persistent peer connections / a relay daemon.
