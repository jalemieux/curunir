// Conversation-sidebar logic for frames that belong to a conversation the
// user isn't looking at. The socket carries every session's traffic, and one
// kind of conversation starts with no action from the user: a handoff from a
// peer container (`handoff:<id>`). Its reply used to be discarded, so the row
// only showed up after a reload.
//
// Pure helpers (no DOM, no socket) so they can be tested under node — see
// tests/js/test_sidebar_unread.mjs. index.html owns the rendering.

// The final frame of an agent turn in a session other than the open one.
// Typed frames (snapshots, status) and streaming deltas don't count: the
// transcript is saved just before the final frame goes out, so that is the
// first moment a conversations_request can return the new row.
export function isBackgroundFinal(msg, activeSessionId) {
  return !!msg && !msg.type && msg.final === true &&
    !!msg.session_id && msg.session_id !== activeSessionId;
}

// The page's onUnhandledFrame hook. `unread` maps session id → the agent the
// frame was stamped with ("" on a single-agent container). Returns true when
// the frame was consumed.
export function routeUnhandledFrame(msg, { unread, activeSessionId, onSnapshot, onBackgroundFinal }) {
  if (msg && msg.type === "conversations_snapshot") {
    onSnapshot(msg.conversations || []);
    return true;
  }
  if (isBackgroundFinal(msg, activeSessionId)) {
    unread.set(msg.session_id, msg.agent || "");
    onBackgroundFinal(msg);
    return true;
  }
  return false;
}

// Opening a conversation is what reads it.
export function markRead(unread, sessionId) {
  return unread.delete(sessionId);
}

// Class list for one sidebar row.
export function rowClass(conv, activeSessionId, unread) {
  let cls = "conv-row";
  if (conv.session_id === activeSessionId) cls += " active";
  else if (unread.has(conv.session_id)) cls += " unread";
  if (conv.channel === "handoff") cls += " handoff";
  return cls;
}

// Names of agents other than `currentAgent` holding an unread conversation —
// their rows aren't in the visible list, so the agent picker carries the cue.
export function agentsWithUnread(unread, currentAgent) {
  const names = new Set();
  for (const agent of unread.values()) {
    if (agent && agent !== currentAgent) names.add(agent);
  }
  return names;
}
