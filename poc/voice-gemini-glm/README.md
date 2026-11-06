# POC: realtime voice with Gemini Live + GLM

A standalone experiment, not part of the curunir runtime. A browser talks to
Gemini Live through a small FastAPI backend. Three modes:

- **A, Gemini only**: Gemini answers everything itself.
- **B, Gemini + GLM**: Gemini delegates substantive questions to GLM (via
  OpenRouter) through one function, `ask_agent(question)`, and speaks the
  answer. Small talk stays with Gemini.
- **C, Gemini as voice only**: every user turn goes through `ask_agent`, and
  Gemini is told to speak the answer word for word. GLM is the only brain;
  every turn pays its latency.

The page measures the latency of every stage so the modes can be compared on
the same questions. In modes B and C it also counts how many turns Gemini
actually delegated, because the Live API cannot force a function call: the
prompt asks, the model decides.

```
browser mic (PCM16 16 kHz) --ws--> server.py --Gemini Live--> gemini-3.8-live
                                      |  ask_agent(question)        |
                                      +--> OpenRouter (z-ai/glm-5.2) |
browser speakers (PCM16 24 kHz) <--ws-- server.py <-- audio ---------+
```

## Setup

Python 3.11+ (the SDK requires it).

```bash
cd poc/voice-gemini-glm
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill in GEMINI_API_KEY and OPENROUTER_API_KEY
python server.py        # http://127.0.0.1:8787
```

Open the page in Chrome, pick a mode, press Connect and allow the microphone.
Use headphones: the browser's energy VAD and Gemini's barge-in both react to
speaker bleed. A new Gemini session is created on every Connect, so switch
modes by disconnecting first.

Everything is configured in `.env` (see `.env.example`): models, voice, GLM
timeout, the agent endpoint (`AGENT_BASE_URL`, any OpenAI-compatible server,
so a local Ollama or LM Studio model can replace GLM), and whether `ask_agent` is BLOCKING (Gemini waits silently) or
NON_BLOCKING (Gemini may keep talking and the answer arrives with
`scheduling=INTERRUPT`).

## What the page shows

- **Latency per turn**, in milliseconds. "speech end" is the last voice heard by
  the browser's energy VAD (converted to the server clock with a ping-based
  offset). "first audio" is the first audio chunk received from Gemini on the
  server. "playback" is when the first sample of the reply left the browser's
  playback queue. The GLM columns are measured on the server around the
  OpenRouter request (streaming, so first token is visible).
- **Transcript** from Gemini's input and output transcription.
- **Events**: Gemini setup, voice activity signals if the model sends them, tool
  calls, GLM results, interruptions, cancellations, `go_away`.

Server logs carry the same events.

## Interruptions

Speaking over Gemini makes it send `interrupted`; the server forwards a `clear`
message, the browser drops its playback queue, and the turn is closed as
interrupted. If Gemini cancels a pending `ask_agent` call
(`tool_call_cancellation`), the GLM request still finishes but its result is
dropped instead of being sent back, so no stale answer is spoken.

## Tests

```bash
.venv/bin/python -m pytest -q tests
```

The tests cover the OpenRouter streaming client (answer, first-token timing,
timeout, HTTP error, empty answer) and the latency arithmetic. The Gemini
Live path needs a key and a microphone and is exercised by hand.

## Test scenarios (section 4 of the brief)

| Test                      | How                                                     |
| ------------------------- | ------------------------------------------------------- |
| General conversation      | Mode A, or small talk in mode B: no tool call logged    |
| Every turn delegated      | Mode C: "delegated n/n turns" next to the status        |
| Question delegated to GLM | Mode B: `tool_call` event, then `glm_done`, then audio  |
| Long GLM response         | Ask for a detailed explanation; read GLM total column   |
| Interrupt Gemini          | Talk over it: `clear` sent, row marked interrupted      |
| Follow-up question        | Ask "and why?" after an answer                          |
| GLM timeout/error         | Set `GLM_TIMEOUT_S=0.001` or a wrong key; Gemini says the agent did not answer |

Results and the bottleneck analysis go in `FINDINGS.md`.
