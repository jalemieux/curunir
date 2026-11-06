// Browser side of the voice POC. See server.py for the WebSocket protocol.
const $ = (id) => document.getElementById(id);
let ws, micCtx, outCtx, micStream, playerNode, mode;
const t0 = Date.now();

function logLine(text) {
  const el = $("log");
  el.textContent += `${((Date.now() - t0) / 1000).toFixed(3)}  ${text}\n`;
  el.scrollTop = el.scrollHeight;
}

function setStatus(s) { $("status").textContent = s; }

$("connect").onclick = () => (ws ? disconnect() : connect());

async function connect() {
  mode = document.querySelector("input[name=mode]:checked").value;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws?mode=${mode}`);
  ws.binaryType = "arraybuffer";
  setStatus("connecting");
  $("connect").textContent = "Disconnect";
  ws.onmessage = (e) => {
    if (typeof e.data === "string") onServerEvent(JSON.parse(e.data));
    else playerNode?.port.postMessage({ type: "audio", pcm: e.data }, [e.data]);
  };
  ws.onclose = () => { stopAudio(); ws = null; setStatus("disconnected"); $("connect").textContent = "Connect"; logLine("websocket closed"); };
  ws.onerror = () => logLine("websocket error");
}

function disconnect() { ws?.close(); }

async function onServerEvent(ev) {
  switch (ev.type) {
    case "status":
      logLine(`status ${JSON.stringify(ev)}`);
      if (ev.state === "connected") {
        setStatus(`connected (${ev.mode}, ${ev.model})`);
        await syncClock();
        await startAudio();
        setStatus(`live (${ev.mode}, ${ev.model})`);
      } else if (ev.state === "loading") {
        setStatus(ev.detail);
      } else if (ev.state === "error") {
        setStatus(`error: ${ev.detail}`);
      }
      break;
    case "pong":
      pongs.get(ev.t_client)?.(ev);
      break;
    case "event":
      { const { type, name, t, turn, ...rest } = ev; logLine(`turn ${turn} ${name} ${Object.keys(rest).length ? JSON.stringify(rest) : ""}`);
        if (rest.delegated_turns && mode !== "gemini") $("delegated").textContent = `delegated ${rest.delegated_turns} turns`; }
      break;
    case "transcript":
      { const el = $("transcript"); el.innerHTML += `<span class="${ev.who}">${ev.who}:</span> ${escapeHtml(ev.text)}\n`; el.scrollTop = el.scrollHeight; }
      break;
    case "audio_start":
      playerNode?.port.postMessage({ type: "mark", turn: ev.turn });
      break;
    case "clear":
      playerNode?.port.postMessage({ type: "clear" });
      break;
    case "turn":
      renderTurn(ev);
      break;
  }
}

// ---- clock sync: estimate (server clock - Date.now()) from the ping with the smallest round trip.
const pongs = new Map();
async function syncClock() {
  let best = null;
  for (let i = 0; i < 5; i++) {
    const t_client = Date.now() + i * 0.001; // unique key
    const pong = await new Promise((resolve) => { pongs.set(t_client, resolve); ws.send(JSON.stringify({ type: "ping", t_client })); });
    pongs.delete(t_client);
    const rtt = Date.now() - t_client;
    const offset = pong.t_server - (t_client + rtt / 2);
    if (!best || rtt < best.rtt) best = { rtt, offset };
  }
  ws.send(JSON.stringify({ type: "clock", offset_ms: best.offset, rtt_ms: Math.round(best.rtt) }));
}

// ---- audio
async function startAudio() {
  outCtx = new AudioContext({ sampleRate: 24000 });
  await outCtx.audioWorklet.addModule("/static/player-worklet.js");
  playerNode = new AudioWorkletNode(outCtx, "player");
  playerNode.connect(outCtx.destination);
  playerNode.port.onmessage = (e) => {
    if (e.data.type === "playback_start") send({ type: "playback_start", turn: e.data.turn, t_client: Date.now() });
  };

  micCtx = new AudioContext({ sampleRate: 16000 });
  await micCtx.audioWorklet.addModule("/static/mic-worklet.js");
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  const source = micCtx.createMediaStreamSource(micStream);
  const micNode = new AudioWorkletNode(micCtx, "mic", { numberOfOutputs: 1 });
  const mute = micCtx.createGain();
  mute.gain.value = 0;
  source.connect(micNode).connect(mute).connect(micCtx.destination);
  micNode.port.onmessage = (e) => {
    const m = e.data;
    if (m.type === "pcm") { if (ws?.readyState === 1) ws.send(m.buf); }
    else if (m.type === "speech_end") send({ type: "speech_end", t_client: Date.now() - m.silence_ms });
    else if (m.type === "speech_start") {
      // Gemini modes: stop local playback at once on barge-in; the server may have finished
      // sending a reply that is still playing here. In local mode the server's VAD decides.
      if (mode !== "local") playerNode?.port.postMessage({ type: "clear" });
      send({ type: "speech_start" });
    }
    else if (m.type === "level") {
      $("level").value = m.rms;
      $("levelText").textContent = `${m.rms.toFixed(3)} ${m.speaking ? "speaking" : ""}`;
    }
  };
  logLine(`audio started: mic ${micCtx.sampleRate} Hz, out ${outCtx.sampleRate} Hz, output latency ${Math.round((outCtx.outputLatency || 0) * 1000)} ms`);
}

function stopAudio() {
  micStream?.getTracks().forEach((t) => t.stop());
  micCtx?.close(); outCtx?.close();
  micCtx = outCtx = micStream = playerNode = null;
}

function send(obj) { if (ws?.readyState === 1) ws.send(JSON.stringify(obj)); }

// ---- latency table
function renderTurn(t) {
  const d = t.durations;
  const id = `turn-${t.n}`;
  let row = document.getElementById(id);
  if (!row) { row = document.createElement("tr"); row.id = id; $("turns").querySelector("tbody").appendChild(row); }
  const cell = (k) => (d[k] === undefined ? "" : d[k]);
  const note = [t.interrupted ? "interrupted" : "", t.speech_end ? "" : "no speech end", t.question ? `q: ${t.question}` : ""].filter(Boolean).join("; ");
  row.innerHTML = [t.n, mode, cell("speech_end_to_first_audio"), cell("speech_end_to_playback"), cell("speech_end_to_tool_call"),
    cell("stt"), cell("glm_first_token"), cell("glm_total"), cell("tts_first"), cell("glm_done_to_first_audio"), t.answer_chars || "",
    mode === "gemini" ? "" : mode === "local" ? "yes" : (t.tool_call ? "yes" : "no"), escapeHtml(note)]
    .map((v) => `<td>${v}</td>`).join("");
}

function escapeHtml(s) { return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
