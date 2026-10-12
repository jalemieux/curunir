# Voice mode: low-latency build plan (#551)

**Status:** plan only, no implementation. Written 2026-09-27 for
[#551](https://github.com/jalemieux/curunir/issues/551). Builds on the
`voice-client` prototype (PR #515) and the earlier plan-only PRs #293
(mobile voice MVP, #292) and #524 (xAI Voice Agent Builder + MCP, #514).

## TL;DR

- **The prototype is slow because nothing overlaps.** Each stage waits for the
  previous one to finish: client STT, the whole agent turn, TTS of the whole
  reply, then a single base64 MP3 relayed and downloaded before playback
  starts. Time to first audio (TTFA) is roughly the *sum* of every stage.
  Streaming turns that into the *first chunk* of each stage.
- **Recommendation: a streamed cascade with curunir as the only brain.** Chunk
  the text deltas we already stream (`on_text_delta`) into sentences, send
  each sentence to a streaming TTS, and push the audio to the client as small
  `audio_chunk` frames it plays as they arrive. The agent loop, tools, memory,
  persona, and history stay unchanged. Put STT and TTS behind an
  OpenAI-compatible provider seam so that choosing hosted or local is a config
  setting, not a code change.
- **Speech-to-speech realtime models come later, as a separate experiment**
  (Phase 4). They are the fastest option for small talk, but they create a
  second brain: a split transcript, drifting persona and memory, and tool
  results passing through two models. Try them only after the cascade has
  numbers to compare against. Telephony through xAI or MCP stays in #514's
  lane.
- **Targets:** p50 TTFA **≤ 1.5 s** for no-tool turns in Phase 1 (provisional:
  the number is confirmed or reset from the Phase 0 measurement) and ≤ 1.0 s
  after the Phase 2/3 work. Tool turns speak a short acknowledgment within
  ≤ 1.5 s, then the answer.
- **Barge-in** needs two things `request_cancel` alone doesn't give (see §4).
  The model writes faster than a voice reads, so the agent turn has usually
  *finished* while audio is still playing, and `request_cancel` is a no-op
  then: the speaker needs its own per-session stop signal. And cancel is only
  checked between loop iterations, so an interrupt during a long streamed
  generation doesn't stop it.

## 1. Latency budget

### Where time goes today (`voice-client`, estimated; Phase 0 measures it)

| # | Stage | Serial today | Notes |
|---|-------|--------------|-------|
| 1 | Endpointing + STT (on-device, iOS PTT app) | 0.3–1.0 s | Starts after the user releases PTT. |
| 2 | Client → portal (Render) → container relay | 0.1–0.3 s | Two WS hops. |
| 3 | Agent turn: LLM TTFT | 0.8–3 s | Model- and cache-dependent. |
| 4 | Agent turn: tool iterations | 0 – 10+ s | Each iteration is another full LLM call plus the tool itself. |
| 5 | Agent turn: generating the full reply | 2–6 s | About 100–200 words, fully generated before TTS starts. |
| 6 | Whole-reply TTS (`tts-1`, MP3) | 1–4 s | Scales with reply length. Nothing plays until it finishes. |
| 7 | Base64 MP3 inlined on the final frame, relayed, decoded | 0.2–1 s | A ~150 KB reply is ~200 KB of base64 through two WS hops. |
| | **TTFA, no tools** | **~5–15 s** | Every stage is on the critical path. |

Stages 5, 6 and 7 are the ones streaming can collapse. Stage 4 can't be
removed (the tools are the point), but it can be *masked* by speaking before
and between tool calls.

### Streamed-cascade budget (target, no-tool turn)

| Stage | Budget |
|-------|--------|
| End of speech → final transcript (VAD endpointing + streaming STT finalize) | 300–500 ms |
| Relay to container | ≤ 150 ms |
| LLM TTFT (warm prefix cache) | 400–900 ms |
| First sentence accumulated (≈ 8–15 words, or a clause break) | 150–400 ms |
| TTS time-to-first-byte (streaming PCM, played as it arrives) | 150–300 ms |
| Relay + client jitter buffer | ≤ 150 ms |
| **TTFA** | **≈ 1.2–2.4 s p50 in Phase 1 → ≤ 1.0 s with the Phase 2/3 levers** |

The Phase 2/3 levers are streaming STT with early finalize, a shorter
first-chunk rule, and a faster or local TTS. The biggest single lever, a
smaller or faster model for voice turns (`VOICE_MODEL`), is in Phase 1
(item 9): LLM TTFT alone is 0.8–3 s today, so without it Phase 1 can't reach
its own target.

**LLM TTFT dominates what remains**, so the voice path must keep the prefix
cache warm. The existing cache-safe design (static system prompt, per-session
"started at", live time note at the suffix) already does this. The voice
style note must keep riding the ephemeral `turn_note` (as `voice-client`
2b68084 does), never the system prompt.

## 2. Architecture options

| | A. Streamed cascade *(recommended)* | B. Realtime S2S front end + curunir delegate | C. Hosted voice-agent platform (#514) | D. Local models |
|---|---|---|---|---|
| What | STT → curunir agent (streaming text) → sentence-chunked streaming TTS | Realtime speech-to-speech model handles the conversation. One tool, `ask_curunir`, runs `Agent.handle` on the same session. | xAI Voice Agent Builder (or similar) owns telephony, STT, TTS and turn-taking. Calls curunir over MCP. | Same as A (or B), with STT/TTS (and optionally the LLM) on the host or a GPU box |
| TTFA, no tools | ~1–2 s | ~0.5–1 s (third-party benchmarks put hosted S2S at ~0.8 s end to end) | Similar to B | Hardware-bound, ~0.5–1.5 s on a GPU box |
| TTFA, tool turns | Spoken ack, then the answer after the tools | Front end talks immediately and fills while curunir works | Same as B | Same as A |
| Fit with the agent loop | **Native.** One brain, one history, all tools, persona and memory. | Two brains. The front end needs curunir's persona and recent history injected, and must write its transcript back into the curunir session. | Curunir is a tool server only. No history or persona continuity unless it is rebuilt there. | Native (A) |
| Cost | Main-LLM tokens + STT/TTS per minute (cheap) | Main LLM + realtime audio tokens (the most expensive) | Platform per-minute + main LLM on delegation | Hardware + electricity only |
| Quality risk | Cascade prosody is flatter; mitigated by TTS `instructions` / expressive voices | Best conversational feel; risk of the front end answering from its own knowledge (breaks the grounding guardrail) | Voice and persona controlled by the platform | Open-weight TTS quality varies by model |
| New surface | `audio_chunk` frames + client player | Realtime WS bridge + tool bridge | MCP server (#524) + public exposure | Sidecar service + compose override |

**Why A first.** Curunir's value is the tool-calling agent with memory and
persona. Option A keeps every existing feature working, reuses the streaming
we already have, and turns hosted-vs-local into a provider choice (D becomes a
configuration of A). B is worth trying for the "instant small talk" feel, but
it needs its own work on the grounding guardrail (the front end must delegate
factual questions rather than answer from training), and it doubles cost. The
Phase 1 metrics tell us whether B is worth that.

**Model landscape (snapshot as of September 2026, re-check at build time).**
Hosted realtime S2S: OpenAI `gpt-realtime` family (a `-mini` tier exists),
Google Gemini Live, xAI Grok Voice Agent, Amazon Nova Sonic. Hosted streaming
TTS/STT: OpenAI `gpt-4o-mini-tts` / `gpt-4o(-mini)-transcribe` (the TTS takes
style `instructions`; `tts-1` does not), plus ElevenLabs and Cartesia-class
providers. Open-weight streaming STT: Parakeet TDT (CPU ONNX viable), Kyutai
STT (delayed-streams), whisper.cpp streaming. Open-weight TTS: Kokoro-82M
(small, permissive license), Kyutai TTS / Pocket TTS (streaming, CPU
real-time), Piper (runs on a Pi), and newer larger models that need a ~12 GB
GPU. Choose models in Phase 0/3 by *measured* TTFA on our hosts, not by
benchmark headlines.

### The provider seam (enables D and model swaps)

`src/voice/providers.py` exposes two small async interfaces:

- `SpeechToText.transcribe(audio) -> str` and optionally
  `stream(frames) -> AsyncIterator[Partial|Final]`
- `TextToSpeech.stream(text, *, voice, instructions) -> AsyncIterator[bytes]`
  (raw PCM, 16-bit mono 24 kHz: `response_format="pcm"`)

The default implementation speaks the **OpenAI-compatible audio API**
(`/v1/audio/speech` with streaming, `/v1/audio/transcriptions`). A
`base_url` + model env var then points it at OpenAI, or at a local server
exposing the same API (e.g. a Kokoro/Parakeet sidecar such as `speaches` or
`kokoro-fastapi`). This is the same pattern `docs/local-llm.md` uses for the
LLM (`api_base`). Adapters for other APIs (ElevenLabs, Cartesia) are only
added if a measured win justifies them.

## 3. Streamed cascade design

```
client mic ─VAD─► transcript ─► user_message{voice:true} ─► in_queue ─► agent_worker
                                                                 │
                              Agent.handle(on_text_delta=speaker.feed, turn_note=VOICE_NOTE)
                                                                 │ text deltas
                                                      SentenceChunker ─► TTS.stream ─► audio_chunk frames
                                                                                          │
client ◄─ plays chunks as they arrive (jitter buffer) ◄── route_outbound ◄── out_queue ◄──┘
```

- **`VoiceSpeaker` (new, started by `agent_worker`, channel-agnostic).**
  Created per voice turn. It wraps the existing `on_text_delta`: text deltas
  still go out for captions and the transcript, and are also fed to a
  `SentenceChunker`.
  - *It runs as its own task and outlives `handle()`.* The worker does not
    wait for the audio: it sends the text `final` frame and moves on to the
    next message, while the speaker keeps synthesizing and sending. Waiting
    would block every other session on that agent behind one reply's audio.
  - *First-chunk rule (hard-coded):* flush at the first sentence end, or at a
    clause break (`,;:—`) once ≥ 8 words have accumulated. After that, flush
    on sentence ends to keep prosody natural.
  - *Speakable normalization:* strip markdown (code fences become "I've put
    the code in the chat", tables are skipped, links become their text,
    emoji dropped) with cheap regex. No LLM rewrite; the source is steered by
    the voice turn note, as the prototype already does.
  - One TTS request per sentence, run **ahead** with bounded concurrency
    (e.g. 2). Audio is emitted strictly in sequence order.
  - *Unstreamed final text is spoken too.* Provider errors, "conversation is
    too long" and "Sorry, I encountered an error" are returned as the final
    text and never pass through `on_text_delta`. When the turn ends, the
    speaker speaks whatever part of the final text it was not fed as deltas.
    The same rule covers a stream that breaks mid-sentence (`call_llm` does
    not retry once text has gone out): the error text is spoken instead of
    the voice just stopping. Otherwise failures are dead air and the user
    can't tell a crash from a slow tool.
- **Tool turns.** The voice turn note asks the model to say one short
  acknowledgment sentence *before* calling tools ("Let me check your
  calendar."). Text in a tool-calling iteration already streams as deltas, so
  it gets spoken with no loop changes.
- **Wire protocol (additive).** A new non-final outbound frame:
  `{"type":"audio_chunk","agent","session_id","turn_id","seq","sentence","mime","data"(b64),"text","final"}`.
  - **One format: raw PCM** (`mime: "audio/pcm;rate=24000"`, 16-bit mono).
    A frame is a ~100 ms *slice* of the TTS byte stream, forwarded as it
    arrives, not a whole sentence, so playback starts on the first slice
    (this is what the TTS-TTFB line in the §1 budget assumes). Ogg/Opus was
    the first draft and is dropped: `MediaSource` takes WebM/MP4, not Ogg,
    older iPhones have no `MediaSource`, and WebAudio `decodeAudioData` only
    decodes a whole file, never a slice of one. PCM needs no decoder on any
    client and is about 64 KB/s as base64. Revisit compression only if the
    Phase 0 relay measurement says bandwidth is a problem.
  - `seq` counts frames in the turn; `sentence` is the index of the sentence
    the slice belongs to. `text` is that sentence, sent on its first slice,
    for captions and to track what was actually heard.
  - **End of turn, one rule.** The text `final` frame goes out as soon as
    `handle()` returns, as today. The audio ends later with its own last
    frame, `audio_chunk.final=true` (empty `data` is allowed, and it is sent
    on a stop as well). Clients end the *spoken* turn (re-arm the mic in
    hands-free mode) on the audio end, never on the text `final`.
  - `agent` is stamped like on every other outbound frame (per-agent
    workers on `main`).
  - This needs an `audio: dict | None` field on `OutgoingMessage`, plus
    pass-through in `portal.py`, `local_web.py` and `ws.py` send (the same
    places that forward `delta` today) and the portal relay, which drops
    unknown frame types today.
  - Clients that don't understand `audio_chunk` ignore it. There is no
    whole-reply MP3 fallback: one path to build and test.
- **Client player.** Web converts each PCM slice to a WebAudio `AudioBuffer`
  and schedules it back to back, with a ~100 ms jitter buffer. iOS native
  uses `AVAudioEngine` scheduled buffers. Playback must start on the first
  frame.

## 4. Turn-taking: VAD, barge-in, interruption

- **Endpointing (end of user turn).** Client-side VAD: Silero VAD via
  `onnxruntime-web` on web (vendored, no CDN, same offline rule as the local
  UI charts), and the platform VAD / Silero on iOS. The silence threshold is
  tunable (default ~500 ms). With server streaming STT (Phase 2), the STT's
  own endpointing can finalize early.
- **Barge-in.** While audio is playing and the VAD detects user speech for
  more than ~250 ms (to reject coughs and echo), the client does two things
  at once:
  - Stops playback locally and flushes its buffer. This is instant, and the
    only part the user perceives.
  - Sends the existing `{"command":"interrupt"}` frame, with the last fully
    played `seq`. Every channel already routes it to `request_cancel`
    (through the container, with the agent name).
- **Gap to close: the turn is usually over when the user interrupts.** The
  model writes text much faster than a voice reads it, so in the common case
  `handle()` has already returned while audio is still being made and
  played. `request_cancel` returns `False` and does nothing when no turn is
  running, and the full reply is already saved as "said". So:
  - The speaker has **its own stop signal per session**, set by the
    interrupt frame whether or not a turn is running. On stop it cancels
    in-flight TTS requests, drops queued audio, and sends the closing
    `audio_chunk.final=true`.
  - The **heard-prefix rewrite** (below) runs in `agent_worker` *before* the
    next message of that session is handled. The user's next sentence
    arrives right behind the interrupt, and it must see the corrected
    history.
- **Gap to close: cancel during streaming.** `request_cancel` is checked at
  the top of each loop iteration and before a tool batch (the two
  `cancel_event.is_set()` checks in `Agent.handle`). A cancel that arrives while
  `_consume_stream` is draining a long generation doesn't stop that
  generation. Add a cancel check in the stream consumer (pass the
  `cancel_event` into `call_llm` and break out of the iterator). Record the
  partial assistant text with an `(interrupted)` marker, keeping it
  schema-valid, and never leave a dangling `tool_calls` message (the existing
  `_stub_missing_tool_responses` backstop covers that).
  This benefits text chat too.
- **What the user actually heard.** Once interrupted, the model should know
  its reply was cut off. From the reported `seq` the server finds the last
  fully played sentence and rewrites the persisted assistant message to that
  spoken prefix plus `… (interrupted by user)`, so the next turn doesn't
  assume the user heard everything. This applies to a reply that had already
  finished generating as much as to one cut off mid-stream.
- **Echo.** Use `getUserMedia({echoCancellation:true, noiseSuppression:true})`
  on web and the `AVAudioSession` `.voiceChat` mode (system AEC) on iOS.
  Recommend headphones for speakerphone use in the MVP. Barge-in stays behind
  a client toggle until echo false-triggers are measured.
- **Hands-free loop.** After playback of the `audio_chunk.final=true` frame
  finishes, re-arm the mic automatically (Phase 2). Re-arming on the text
  `final` would record curunir's own voice. Push-to-talk stays the default in the MVP, because
  it is robust and avoids the echo problem.

## 5. Client surface, and what to salvage from `voice-client`

| Surface | Role | Phase |
|---------|------|-------|
| **iOS native PTT app** (CurunirVoice, #515) | Already exists and does on-device STT. It is the fastest way to prove streaming TTS: add an `audio_chunk` player. | 1 |
| **Portal mobile web** (`portal/static/mobile.html`) | The main web surface: mic button + `MediaRecorder` + server STT (per #293), then `audio_chunk` playback (PCM through WebAudio, so no `MediaSource` and older iPhones work). Requires HTTPS (Render already has it). | 1 |
| **Local web console** (`local_web.py`) | Same shared chat module, so the voice UI comes almost free once it's in the portal SPA. Useful for low-latency LAN testing without the Render hop. Loopback is a secure context for the mic; a LAN IP over plain HTTP is not, so document that. | 2 |
| CLI | Out of scope. | — |
| Telephony | #514/#524 (xAI + MCP) only. Not part of this plan. | — |

**Salvage from `voice-client` (PR #515):**

- **Keep:**
  - Portal per-user `client_token` bearer auth (needed for native clients).
  - `IncomingMessage.voice` flag.
  - `turn_note` ephemeral voice style note on `Agent.handle` (cache-safe).
  - `synthesize_speech` extraction (the starting point for the provider
    seam's `TextToSpeech`).
- **Change:** the voice style note should also ask for "one short
  acknowledgment before tools" and "no markdown, no lists longer than three".
  Switch voice-turn TTS to `gpt-4o-mini-tts`: OpenAI documents the
  `instructions` parameter for that model, not for `tts-1`/`tts-1-hd`. The
  prototype's `synthesize_speech` docstring claims the opposite, so its style
  instructions are probably being ignored today. Verify this, and fix the
  docstring when porting.
- **Drop:** whole-reply TTS after the turn and the outbound `audio/mpeg`
  inlining. Streaming replaces them; keeping both means two paths to test.
- **Recommendation:** *port* #515's auth + voice-flag + `turn_note` pieces
  onto `main` as Phase 0, rather than rebase and merge the branch. They are
  independent of the streaming work, but the branch is stale: 58 commits
  behind `main`, 3 conflicts on a trial merge, and its green test run
  (1028 core + 126 portal) dates from July. `main` has since moved to one
  worker per agent with an `agent` field on every frame, which the branch
  predates. Size it as real work, not a merge.

## 6. Phasing

Each bullet below is sized to become one implementation issue.

### Phase 0: measure and land the foundation

1. **Stage timing instrumentation.** Voice turns record timestamps into
   `metadata["stats"]["voice"]`: `recv`, `llm_first_token`, `first_text_delta`,
   `first_tts_byte`, `first_audio_chunk_sent`, `final`. Clients report
   `speech_end` and `first_audio_played` back (a `voice_metrics` frame,
   logged). `python -m src.usage` or a small script prints p50/p90 TTFA.
   Baseline the current `voice-client` numbers first.
   Phase 1's exit number is confirmed or reset from this baseline.
2. **Port #515's reusable pieces onto `main`:** bearer `client_token`,
   `voice` flag, `turn_note`, `synthesize_speech` extraction. A port, not a
   merge (see §5): each piece is re-fitted to per-agent workers and the
   `agent` frame field, with its tests.
3. **Provider seam** (`src/voice/providers.py`): OpenAI-compatible
   `TextToSpeech.stream` (PCM) and `SpeechToText.transcribe`, config-driven
   `base_url`/model. Unit tests with a fake streaming response.

### Phase 1 (MVP): streamed cascade, hosted, push-to-talk

4. `SentenceChunker` + speakable normalizer (pure functions, heavily unit
   tested).
5. `VoiceSpeaker` as its own task started by `agent_worker` (ordered,
   bounded-concurrency TTS, speaks unstreamed final text, closes with
   `audio_chunk.final=true`), `OutgoingMessage.audio`, `audio_chunk`
   pass-through in the portal, local web and WS channels, and in the portal
   relay.
6. iOS app: `audio_chunk` player (in the native client repo; coordinate).
7. Portal mobile web: PTT mic, server STT via the provider seam (#293's
   `transcribe_audio`), `audio_chunk` player.
8. Voice turn-note update (ack-before-tools, speakable style) + switch to an
   instructions-capable TTS model.
9. `VOICE_MODEL` override so voice turns can use a faster LLM (local or
   hosted) than text turns. A few lines, and the one lever that moves LLM
   TTFT, the largest line in the budget.

**Exit criteria:** p50 TTFA ≤ 1.5 s no-tool on the iOS app over the portal,
with a spoken ack ≤ 1.5 s on tool turns, both measured by #1. The 1.5 s is
provisional: set the final number after the Phase 0 baseline, since the §1
budget for this phase is 1.2–2.4 s.

### Phase 2: conversational turn-taking

10. Mid-stream cancellation in `call_llm`/`_consume_stream` + partial-reply
    persistence (also fixes slow text-chat interrupts).
11. Client VAD endpointing (Silero, vendored) + hands-free re-arm on the
    audio end.
12. Barge-in: local playback stop + interrupt frame carrying the last played
    `seq` + the speaker's per-session stop signal (works with no turn
    running) + heard-prefix history rewrite in `agent_worker` before the
    session's next message.
13. Streaming server STT with partials (for web clients) through the provider
    seam.
14. Local web console voice UI (shares the chat module).

### Phase 3: local speech models

15. `docker-compose.voice-local.yml` override: a sidecar exposing
    OpenAI-compatible `/v1/audio/*` (Kokoro or Kyutai TTS, Parakeet or
    whisper.cpp STT). Point `TTS_BASE_URL`/`STT_BASE_URL` at it.
    - Kept out of the curunir image, so it stays slim and multi-arch.
    - Applied per host, the same pattern as `docker-compose.webcam.yml`.
16. Benchmark on our actual hosts (the mini PCs are CPU-only; the Apple
    Silicon/GPU box from `docs/local-llm.md` runs the LLM) and write the
    results into `docs/`. Pick defaults per host class.

### Phase 4: realtime S2S front-end experiment (behind a flag)

17. `VOICE_FRONTEND=realtime`: a bridge from the client audio socket to a
    hosted realtime model session.
    - Its instructions carry curunir's identity + persona prompts and a
      short history tail.
    - Its single tool, `ask_curunir(request)`, runs `Agent.handle` on the same
      `session_id`.
    - The realtime transcript is written back into the curunir session.
    - Required rule: factual or personal questions are delegated, never
      answered from the model's own knowledge (the grounding guardrail).
    - Fast read paths can call #524's read-only MCP tools directly.
    - Camera (cross-link #552 / PR #553, `docs/webcam-plan.md` §6): in the
      cascade, voice reaches the camera only through the opt-in
      `camera(describe)` tool, like any other tool. No voice-specific path.
      An image-capable realtime front end may additionally push frames
      straight from that plan's `latest_frame(camera) -> (bytes, ts)`
      buffer, gated by the same off-limits list (`memory/cameras.md`) and privacy rules.
18. A/B against Phase 2 on TTFA, cost per minute, and grounding (reuse the
    `eval/harness` LLM judge on a voice task set). Decide whether it's worth
    keeping.

## 7. Config, dependencies, gating

**New env vars** (all optional; documented in `.env.example`):

| Var | Default | Purpose |
|-----|---------|---------|
| `TTS_MODEL` / `TTS_VOICE` | exist; change default to `gpt-4o-mini-tts` for voice turns | Voice model and voice |
| `TTS_BASE_URL` / `STT_BASE_URL` | unset (OpenAI) | Point at a local OpenAI-compatible audio server |
| `STT_MODEL` | `gpt-4o-mini-transcribe` | Server STT for web clients |
| `VOICE_MODEL` | unset (uses `MODEL`) | Phase 1: faster LLM for voice turns |
| `VOICE_FRONTEND` / `REALTIME_MODEL` | `cascade` / unset | Phase 4 experiment |

Deliberately not configurable in the first version: the audio format (PCM
only), the first-chunk word count (hard-coded 8), and a streaming on/off
switch (no whole-reply fallback). Add one back when a measurement asks for
it.

**Dependencies:**

- Phases 0–2: none new server-side. The `openai` SDK already supports
  streaming speech; `onnxruntime-web` + a Silero model is vendored into the
  static assets.
- Phase 3: sidecar images only, nothing in `requirements.txt`.
- Phase 4: none if it uses the realtime WS through `websockets` (already a
  dependency).

**Persona/module gating:** none. Voice is a channel capability, like text
streaming. It isn't a vertical module, so it doesn't go in `src/modules.py`.
Not in this plan, each to be added when a measurement or a user asks for
it: a per-persona `prompts/voice.md` style override, a filler sound during
slow silent tool calls, and a timer that flushes a first chunk that has no
punctuation.

## 8. Risks and open questions

- **Portal relay hop.** PCM frames are ~6.4 KB of base64 per 100 ms
  (~64 KB/s), so relaying through Render should add little, but Phase 0 must
  confirm it; if it doesn't hold, that is the measurement that brings a
  compressed format back. The local console gives a no-relay comparison.
- **Browser playback is unverified.** The PCM-through-WebAudio choice rests
  on platform documentation, not on a test in a real browser. Item 7 starts
  with a ten-minute spike on an actual iPhone.
- **Prefix-cache busting.** Any per-voice change to the system prompt would
  hurt TTFT for every turn. Keep all voice steering in `turn_note`. Add a test
  that asserts the system prompt is byte-identical for voice and text turns.
- **Markdown-heavy personas** (finance tables): the normalizer drops tables,
  so the spoken reply should summarize and point to the chat ("I've put the
  breakdown on screen"). The finance guardrail that position tracking is
  tool-backed, never prose, still holds, since the text transcript is
  unchanged.
- **Cost.** Streaming TTS per sentence costs the same per character as whole
  reply TTS. The Phase 4 realtime models are the expensive option, so track
  them via the usage store (TTS/STT usage should also get `UsageRecord`s,
  which is a small extension).
- **Open question for the owner: primary device.** The iOS native app or
  mobile web decides whether item 6 (iOS player) or item 7 (mobile web) goes first. Recommendation: the
  iOS app, since it has STT solved already.
- **Open question for the owner:** is there a GPU box available to curunir
  hosts for Phase 3, or do we target CPU-only open-weight models
  (Kokoro/Parakeet/Pocket TTS) on the mini PCs?
- **Open question for the owner:** is barge-in on speakerphone a
  requirement, or is PTT/headphones acceptable through Phase 1?

## Sources (model landscape snapshot)

- [Real-Time vs Turn-Based Voice Agents 2026 (Softcery)](https://softcery.com/lab/ai-voice-agents-real-time-vs-turn-based-tts-stt-architecture)
- [OpenAI vs Gemini vs Qwen Realtime Voice API 2026 (tech-insider)](https://tech-insider.org/openai-vs-google-vs-qwen-voice-ai-apis-2026/)
- [Real-Time AI Voice Models Compared (MindStudio)](https://www.mindstudio.ai/blog/real-time-ai-voice-models-compared-2025)
- [Local Voice-AI Models, self-hosted STT & TTS (D-Central)](https://d-central.tech/local-voice-ai-models/)
- [Kyutai STT](https://kyutai.org/stt/) · [Kyutai TTS](https://kyutai.org/tts/) · [delayed-streams-modeling](https://github.com/kyutai-labs/delayed-streams-modeling/)
- [Streaming Parakeet transcription (Modal)](https://modal.com/docs/examples/streaming_parakeet)
