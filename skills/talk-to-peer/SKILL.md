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
