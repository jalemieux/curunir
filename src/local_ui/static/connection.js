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

  function connect() {
    ws = new WebSocket(url());
    onStatus && onStatus("reconnecting");

    ws.onopen = () => {
      backoff = 1000;
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

  function send(frame) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(frame));
    return true;
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
