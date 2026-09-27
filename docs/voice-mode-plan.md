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
- **Targets:** p50 TTFA **≤ 1.5 s** for no-tool turns in Phase 1 and ≤ 1.0 s
  after the Phase 2/3 work. Tool turns speak a short acknowledgment within
  ≤ 1.5 s, then the answer.
- **Barge-in** reuses `request_cancel`, with one real gap to close: cancel is
  only checked between loop iterations, so an interrupt during a long
  streamed generation doesn't stop it (see §4).

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
| TTS time-to-first-byte (streaming) | 150–300 ms |
| Relay + client jitter buffer | ≤ 150 ms |
| **TTFA** | **≈ 1.2–2.4 s p50 in Phase 1 → ≤ 1.0 s with the Phase 2/3 levers** |

The Phase 2/3 levers are streaming STT with early finalize, a shorter
first-chunk rule, a faster or local TTS, and a smaller or faster model for
voice turns (`VOICE_MODEL` override, optional).

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

The default implementation speaks the **OpenAI-compatible audio API**
(`/v1/audio/speech` with streaming, `/v1/audio/transcriptions`). A
`base_url` + model env var then points it at OpenAI, or at a local server
exposing the same API (e.g. a Kokoro/Parakeet sidecar such as `speaches` or
`kokoro-fastapi`). This is the same pattern `docs/local-llm.md` uses for the
LLM (`api_base`). Adapters for other APIs (ElevenLabs, Cartesia) are only
added if a measured win justifies them. `synthesize_speech` from
`voice-client` becomes the non-streaming wrapper around
`TextToSpeech.stream`.

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

- **`VoiceSpeaker` (new, in `agent_worker`, channel-agnostic).** Created per
  voice turn. It wraps the existing `on_text_delta`: text deltas still go out
  for captions and the transcript, and are also fed to a `SentenceChunker`.
  - *First-chunk rule:* flush at the first sentence end, or at a clause break
    (`,;:—`) once ≥ N words have accumulated, or after 600 ms with no
    punctuation. After that, flush on sentence ends to keep prosody natural.
  - *Speakable normalization:* strip markdown (code fences become "I've put
    the code in the chat", tables are skipped, links become their text,
    emoji dropped) with cheap regex. No LLM rewrite; the source is steered by
    the voice turn note, as the prototype already does.
  - One TTS request per chunk, run **ahead** with bounded concurrency
    (e.g. 2). Audio is emitted strictly in sequence order.
- **Tool turns.** The voice turn note asks the model to say one short
  acknowledgment sentence *before* calling tools ("Let me check your
  calendar."). Text in a tool-calling iteration already streams as deltas, so
  it gets spoken with no loop changes. For a silent tool call longer than
  ~2 s, the speaker can optionally emit a local earcon or filler phrase
  (client-side, cheap, no LLM).
- **Wire protocol (additive).** A new non-final outbound frame:
  `{"type":"audio_chunk","session_id","turn_id","seq","mime","data"(b64),"text","final"}`.
  - `mime` defaults to `audio/ogg;codecs=opus` for web/iOS, with `pcm16` as
    an option for native clients.
  - `text` is the spoken sentence, for captions and to track what was
    actually heard.
  - This needs an `audio: dict | None` field on `OutgoingMessage`, plus
    pass-through in `portal.py`, `local_web.py` and `ws.py` send (the same
    places that forward `delta` today) and the portal relay.
  - Clients that don't understand `audio_chunk` ignore it. The existing
    inlined whole-reply MP3 path from #515 stays as a fallback
    (`VOICE_STREAMING=false`).
- **Client player.** Web uses `MediaSource` (opus/webm) or WebAudio decode
  per chunk with a ~100 ms jitter buffer. iOS native uses `AVAudioEngine`
  scheduled buffers. Playback must start on the first chunk.

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
  - Sends the existing `{"command":"interrupt"}` frame, which reaches
    `agent.request_cancel(session_id)` in every channel already.
  The server's `VoiceSpeaker` also cancels in-flight TTS requests and drops
  queued chunks on cancel.
- **Gap to close: cancel during streaming.** `request_cancel` is checked at
  the top of each loop iteration and before a tool batch
  (`src/agent/agent.py` ~L684/L743). A cancel that arrives while
  `_consume_stream` is draining a long generation doesn't stop that
  generation. Add a cancel check in the stream consumer (pass the
  `cancel_event` into `call_llm` and break out of the iterator). Record the
  partial assistant text with an `(interrupted)` marker, keeping it
  schema-valid, and never leave a dangling `tool_calls` message (the existing
  `_stub_missing_tool_responses` backstop covers that).
  This benefits text chat too.
- **What the user actually heard.** Once interrupted, the model should know
  its reply was cut off. The client reports the last fully played `seq`.
  The server rewrites the persisted assistant message to the spoken prefix
  plus `… (interrupted by user)`, so the next turn doesn't assume the user
  heard everything.
- **Echo.** Use `getUserMedia({echoCancellation:true, noiseSuppression:true})`
  on web and the `AVAudioSession` `.voiceChat` mode (system AEC) on iOS.
  Recommend headphones for speakerphone use in the MVP. Barge-in stays behind
  a client toggle until echo false-triggers are measured.
- **Hands-free loop.** After playback finishes, re-arm the mic
  automatically (Phase 2). Push-to-talk stays the default in the MVP, because
  it is robust and avoids the echo problem.

## 5. Client surface, and what to salvage from `voice-client`

| Surface | Role | Phase |
|---------|------|-------|
| **iOS native PTT app** (CurunirVoice, #515) | Already exists and does on-device STT. It is the fastest way to prove streaming TTS: add an `audio_chunk` player. | 1 |
| **Portal mobile web** (`portal/static/mobile.html`) | The main web surface: mic button + `MediaRecorder` + server STT (per #293), then `audio_chunk` playback. Requires HTTPS (Render already has it). | 1 |
| **Local web console** (`local_web.py`) | Same shared chat module, so the voice UI comes almost free once it's in the portal SPA. Useful for low-latency LAN testing without the Render hop. Loopback is a secure context for the mic; a LAN IP over plain HTTP is not, so document that. | 2 |
| CLI | Out of scope. | — |
| Telephony | #514/#524 (xAI + MCP) only. Not part of this plan. | — |

**Salvage from `voice-client` (PR #515):**

- **Keep:**
  - Portal per-user `client_token` bearer auth (needed for native clients).
  - `IncomingMessage.voice` flag.
  - `turn_note` ephemeral voice style note on `Agent.handle` (cache-safe).
  - `synthesize_speech` extraction (becomes the non-streaming TTS wrapper).
  - Outbound `audio/mpeg` inlining (as the fallback path).
- **Change:** the voice style note should also ask for "one short
  acknowledgment before tools" and "no markdown, no lists longer than three".
  Switch voice-turn TTS to `gpt-4o-mini-tts`: OpenAI documents the
  `instructions` parameter for that model, not for `tts-1`/`tts-1-hd`. The
  prototype's `synthesize_speech` docstring claims the opposite, so its style
  instructions are probably being ignored today. Verify this, and fix the
  docstring when merging.
- **Drop as the primary path:** whole-reply TTS after the turn.
- **Recommendation:** rebase and merge #515's auth + voice-flag + `turn_note`
  pieces as Phase 0. They are independent of the streaming work and already
  tested (1028 core + 126 portal tests green at the time).

## 6. Phasing

Each bullet below is sized to become one implementation issue.

### Phase 0: measure and land the foundation

1. **Stage timing instrumentation.** Voice turns record timestamps into
   `metadata["stats"]["voice"]`: `recv`, `llm_first_token`, `first_text_delta`,
   `first_tts_byte`, `first_audio_chunk_sent`, `final`. Clients report
   `speech_end` and `first_audio_played` back (a `voice_metrics` frame,
   logged). `python -m src.usage` or a small script prints p50/p90 TTFA.
   Baseline the current `voice-client` numbers first.
2. **Merge #515's reusable pieces:** bearer `client_token`, `voice` flag,
   `turn_note`, `synthesize_speech` extraction.
3. **Provider seam** (`src/voice/providers.py`): OpenAI-compatible
   `TextToSpeech.stream` and `SpeechToText.transcribe`, config-driven
   `base_url`/model. Unit tests with a fake streaming response.

### Phase 1 (MVP): streamed cascade, hosted, push-to-talk

4. `SentenceChunker` + speakable normalizer (pure functions, heavily unit
   tested).
5. `VoiceSpeaker` in `agent_worker` (ordered, bounded-concurrency TTS),
   `OutgoingMessage.audio`, `audio_chunk` pass-through in the portal, local
   web and WS channels, and in the portal relay.
6. iOS app: `audio_chunk` player (in the native client repo; coordinate).
7. Portal mobile web: PTT mic, server STT via the provider seam (#293's
   `transcribe_audio`), `audio_chunk` player.
8. Voice turn-note update (ack-before-tools, speakable style) + switch to an
   instructions-capable TTS model.

**Exit criteria:** p50 TTFA ≤ 1.5 s no-tool on the iOS app over the portal,
with a spoken ack ≤ 1.5 s on tool turns, both measured by #1.

### Phase 2: conversational turn-taking

9. Mid-stream cancellation in `call_llm`/`_consume_stream` + partial-reply
   persistence (also fixes slow text-chat interrupts).
10. Client VAD endpointing (Silero, vendored) + hands-free re-arm.
11. Barge-in: local playback stop + interrupt frame + server TTS cancel +
    heard-prefix history rewrite.
12. Streaming server STT with partials (for web clients) through the provider
    seam.
13. Local web console voice UI (shares the chat module).

### Phase 3: local speech models

14. `docker-compose.voice-local.yml` override: a sidecar exposing
    OpenAI-compatible `/v1/audio/*` (Kokoro or Kyutai TTS, Parakeet or
    whisper.cpp STT). Point `TTS_BASE_URL`/`STT_BASE_URL` at it.
    - Kept out of the curunir image, so it stays slim and multi-arch.
    - Applied per host, the same pattern as `docker-compose.webcam.yml`.
15. Benchmark on our actual hosts (the mini PCs are CPU-only; the Apple
    Silicon/GPU box from `docs/local-llm.md` runs the LLM) and write the
    results into `docs/`. Pick defaults per host class.
16. Optional `VOICE_MODEL` override so voice turns can use a faster LLM
    (local or hosted) than text turns.

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
18. A/B against Phase 2 on TTFA, cost per minute, and grounding (reuse the
    `eval/harness` LLM judge on a voice task set). Decide whether it's worth
    keeping.

## 7. Config, dependencies, gating

**New env vars** (all optional; documented in `.env.example`):

| Var | Default | Purpose |
|-----|---------|---------|
| `VOICE_STREAMING` | `true` | `false` falls back to the whole-reply MP3 path from #515 |
| `TTS_MODEL` / `TTS_VOICE` | exist; change default to `gpt-4o-mini-tts` for voice turns | Voice model and voice |
| `TTS_BASE_URL` / `STT_BASE_URL` | unset (OpenAI) | Point at a local OpenAI-compatible audio server |
| `STT_MODEL` | `gpt-4o-mini-transcribe` | Server STT for web clients |
| `VOICE_AUDIO_FORMAT` | `opus` | `opus` / `pcm16` / `mp3` for `audio_chunk` |
| `VOICE_FIRST_CHUNK_WORDS` | `8` | Chunker tuning |
| `VOICE_MODEL` | unset (uses `MODEL`) | Phase 3: faster LLM for voice turns |
| `VOICE_FRONTEND` / `REALTIME_MODEL` | `cascade` / unset | Phase 4 experiment |

**Dependencies:**

- Phases 0–2: none new server-side. The `openai` SDK already supports
  streaming speech; `onnxruntime-web` + a Silero model is vendored into the
  static assets.
- Phase 3: sidecar images only, nothing in `requirements.txt`.
- Phase 4: none if it uses the realtime WS through `websockets` (already a
  dependency).

**Persona/module gating:** none. Voice is a channel capability, like text
streaming. It isn't a vertical module, so it doesn't go in `src/modules.py`.
Optional later: a persona may ship `prompts/voice.md` to override the voice
style note (for example, a warmer voice for `default` and terser for
`finance`).

## 8. Risks and open questions

- **Portal relay hop.** Opus chunks are small (~2–4 KB per 100 ms), so
  relaying through Render should add little, but Phase 0 must confirm it. The
  local console gives a no-relay comparison.
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
