// connection.js — WebSocket lifecycle node (transport only).
//
// Owns: the socket, JSON parse, auto-reconnect with exponential backoff
// (1s → 30s cap, jittered). Knows nothing about chat, sessions, or frames'
// meaning — it just delivers parsed frames to `onFrame` and surfaces socket
// transitions via `onStatus`. Reusable by the chat pane and (Phase 2) the
// portal's sidebar/scratchpad on the same socket.
//
// Canonical home: src/local_ui/static/. See the design doc:
// docs/superpowers/specs/2026-06-12-local-ui-shared-chat-module-design.md

const MAX_BACKOFF = 30000;

// --- pure outbound helpers (extracted for testability) ---------------------
//
// The socket flaps during reconnect windows; a frame composed while it is
// closed must not be silently dropped. `routeOutbound` decides the fate of an
// outgoing frame, and `drainOutbox` replays buffered frames when the socket
// reopens. Both are pure (no closure/socket state) so they can be unit-tested
// without a browser — see tests/js/test_connection_outbox.mjs.

// routeOutbound(frame, open, outbox) -> { action: "send" | "buffer" | "drop" }
//   Buffers durable frames (chat/slash user messages) into `outbox` when the
//   socket is closed so they replay on reconnect; drops non-durable frames
//   (control/read requests the client re-issues on reconnect anyway).
export function routeOutbound(frame, open, outbox) {
  if (open) return { action: "send" };
  if (frame && frame.durable) { outbox.push(frame); return { action: "buffer" }; }
  return { action: "drop" };
}

// drainOutbox(outbox, isOpen, rawSend): FIFO-flush buffered frames while the
// socket stays open, stopping the moment it reports closed (the remainder
// stays buffered for the next open).
export function drainOutbox(outbox, isOpen, rawSend) {
  while (outbox.length && isOpen()) rawSend(outbox.shift());
}

// createConnection({ url, onFrame, onStatus, onOpen }) -> { send, close, isOpen }
//   url:      () => string   builds the ws URL (local injects ?token=…)
//   onFrame:  (msg)   => {}  every parsed inbound frame
//   onStatus: (state) => {}  socket transition: "reconnecting" | "offline"
//                            (NB: "online" is an agent_status frame, delivered
//                            via onFrame — the socket being open does not mean
//                            the agent is online.)
//   onOpen:   ()      => {}  fired each time the socket opens (after handshake)
export function createConnection({ url, onFrame, onStatus, onOpen }) {
  let ws = null;
  let backoff = 1000;
  let closedByCaller = false;
  const outbox = []; // durable frames buffered while the socket is closed

  function connect() {
    ws = new WebSocket(url());
    onStatus && onStatus("reconnecting");

    ws.onopen = () => {
      backoff = 1000;
      // Replay buffered durable frames (in order) before the caller's onOpen
      // re-issues its control/read requests, so a message composed mid-
      // reconnect reaches the server ahead of a fresh history snapshot.
      drainOutbox(outbox, isOpen, (f) => ws.send(JSON.stringify(f)));
      onOpen && onOpen();
    };
    ws.onmessage = (e) => {
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return; // drop malformed frames rather than throwing in onmessage
      }
      onFrame && onFrame(msg);
    };
    ws.onclose = () => {
      onStatus && onStatus("offline");
      if (closedByCaller) return;
      setTimeout(connect, backoff + Math.random() * 500);
      backoff = Math.min(backoff * 2, MAX_BACKOFF);
    };
    ws.onerror = () => { try { ws.close(); } catch {} };
  }

  // send(frame) -> boolean: true if the frame was delivered OR durably
  //   buffered for replay on reconnect; false only if it was dropped (a
  //   non-durable frame sent while the socket was closed).
  function send(frame) {
    const { action } = routeOutbound(frame, isOpen(), outbox);
    if (action === "send") { ws.send(JSON.stringify(frame)); return true; }
    return action === "buffer";
  }

  function close() {
    closedByCaller = true;
    try { ws && ws.close(); } catch {}
  }

  function isOpen() {
    return !!ws && ws.readyState === WebSocket.OPEN;
  }

  connect();
  return { send, close, isOpen };
}
