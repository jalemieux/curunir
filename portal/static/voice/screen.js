// Pure helpers for the voice screen on the mobile page (#617). No DOM, no socket: the
// page calls these and paints the result, and tests/js/test_voice_screen.mjs runs them
// in node. Loaded as a plain script (window.VoiceScreen) and also readable by node.
(function (root) {
  const STATES = {
    loading:      { cls: "loading",      label: "loading speech models, up to a minute the first time" },
    ready:        { cls: "ready",        label: null },  // depends on the mode, see orbState
    listening:    { cls: "listening",    label: "hearing you" },
    transcribing: { cls: "transcribing", label: "transcribing" },
    thinking:     { cls: "thinking",     label: "thinking" },
    speaking:     { cls: "speaking",     label: "speaking, talk over it or tap the orb to interrupt" },
    off:          { cls: "off",          label: "voice mode ended, tap the mic to start again" },
    unavailable:  { cls: "off",          label: "this agent has no voice yet (it needs the voice release)" },
    error:        { cls: "off",          label: null },
  };

  // The orb's class and the line under it, for a server state and the mode.
  function orbState(state, { ptt = false, talking = false, detail = "" } = {}) {
    if (state === "ready" || state === undefined) {
      return ptt
        ? { cls: talking ? "listening" : "ready", label: talking ? "hearing you" : "hold the button and talk" }
        : { cls: "ready", label: "listening for you" };
    }
    if (state === "error") return { cls: "off", label: detail ? `voice unavailable: ${detail}` : "voice unavailable" };
    const s = STATES[state] || { cls: "ready", label: state };
    return { cls: s.cls, label: s.label };
  }

  // Header pill: where speech runs and how turns end.
  function modePill(provider, serverVad) {
    const where = provider === "local" ? "speech on your box" : provider === "openai" ? "speech via OpenAI" : "speech on the agent";
    return `${where} · ${serverVad ? "hands-free" : "hold to talk"}`;
  }

  // Fold the chat's text frames for the open session into the caption shown while the
  // agent speaks: deltas accumulate, a final replaces, segments after a tool round append.
  function foldCaption(caption, frame) {
    const cur = caption || { text: "", segment: null };
    if (frame.delta) {
      const sameSegment = frame.segment === undefined || frame.segment === cur.segment || cur.segment === null;
      return { text: (sameSegment ? cur.text : cur.text.trimEnd() + " ") + (frame.content || ""), segment: frame.segment ?? cur.segment };
    }
    if (frame.final) {
      const text = frame.content || cur.text;
      return { text: text === "(interrupted)" ? cur.text : text, segment: null };
    }
    return cur;
  }

  // The two timing lines: the user's measurement and the agent's stages.
  function timingLine(firstAudioMs, stages) {
    const parts = [];
    if (typeof firstAudioMs === "number") parts.push(`you stopped → first audio ${(firstAudioMs / 1000).toFixed(1)} s`);
    if (stages && typeof stages.first_audio_chunk_sent === "number") {
      const f = (k) => (typeof stages[k] === "number" ? (stages[k] / 1000).toFixed(1) : null);
      const bits = [["transcript", f("transcript")], ["first text", f("first_text_delta")], ["first audio sent", f("first_audio_chunk_sent")]]
        .filter(([, v]) => v !== null).map(([k, v]) => `${k} ${v}`);
      if (bits.length) parts.push(bits.join(" · ") + " s after speech end");
    }
    return parts;
  }

  // The closing audio frame arrives when synthesis ends, seconds before the phone's
  // speaker is done; the reply is over for the user when its last data slice has played.
  function playbackDone({ closed = false, lastDataSeq = 0, playedSeq = 0 } = {}) {
    return closed && (lastDataSeq === 0 || playedSeq >= lastDataSeq);
  }

  const api = { orbState, modePill, foldCaption, timingLine, playbackDone };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.VoiceScreen = api;
})(typeof window !== "undefined" ? window : globalThis);
