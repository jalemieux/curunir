# Findings

Status: mode D measured on the owner's machine (2026-11-06) and by the bot
with a synthetic driver; modes A to C were tried by the owner but not logged.
The procedure below is fixed so the modes can be compared on the same
questions; the mode A to C rows are still to be filled in.

## Procedure

1. Start the server with keys set, open the page in Chrome with headphones.
2. Mode A: ask each question below, wait for the full reply. Note the
   "speech end → playback" column.
3. Mode B: disconnect, switch mode, connect, ask the same questions.
   Then mode C the same way; note the "delegated n/n turns" counter, which
   says how often Gemini obeyed the always-delegate instruction. Then mode D
   (local voice); its STT and TTS columns say what the local stack costs.
4. Interrupt Gemini mid-sentence once in each mode and check that no stale
   audio plays after the interruption.
5. Copy the table rows here.

Questions (same set in both modes):

1. "What is the capital of Australia?"
2. "Explain in a few sentences why the sky is blue."
3. "Give me three tips for sleeping better."
4. "What did I ask you first?" (follow-up / context check)
5. "Thanks, that's all." (small talk: mode B should not delegate, mode C must)

## Measurements

### Mode D, owner's session, M5 Pro, 2026-11-06 (from voice-poc.log)

Silero VAD on the server, Whisper large-v3-turbo on MLX, GLM 5.2 via
OpenRouter with thinking off, Kokoro. "turn detected" is 500 ms after the
last voice (Silero's silence window). Browser playback start was not logged
server-side, so the last column is server-side first audio.

| Question                   | STT (ms) | GLM first token | GLM total | first token → first audio | turn detected → first audio | speech end → first audio |
| -------------------------- | -------: | --------------: | --------: | ------------------------: | --------------------------: | -----------------------: |
| Can you hear me?           |      189 |             842 |       995 |                       454 |                        1485 |                     1985 |
| What is your name?         |      185 |             296 |       490 |                       676 |                        1158 |                     1658 |
| What model are you using?  |      187 |             385 |       729 |                       962 |                        1535 |                     2035 |
| Who trained you?           |      190 |             296 |      1201 |                      1555 |                        2042 |                     2542 |
| Thank you.                 |      183 |             445 |       751 |                       465 |                        1093 |                     1593 |

Owner's verdict in the thread: "much better" than the first local version
(browser energy VAD as turn detector, GLM thinking on), which was "terrible".

### Mode D, bot's synthetic driver (Kokoro speech plus room noise into the WebSocket, real GLM)

| Run                          | STT | GLM first token | TTS first sentence | speech end → first audio |
| ---------------------------- | --: | --------------: | -----------------: | -----------------------: |
| thinking on, first question  | 207 |            1169 |                264 |                     2290 |
| thinking on, follow-up       | 184 |            1910 |                187 |                     2886 |
| thinking off, first question | 186 |             662 |                364 |                     1744 |
| thinking off, follow-up      | 175 |            1549 |                339 |                     2564 |

GLM first token varies between 0.3 and 1.9 s across runs on OpenRouter for the
same prompt; measured directly, thinking off gives 0.3 to 0.4 s and thinking on
0.7 to 2.3 s.

### Modes A to C

| Question | Mode | speech end → playback (ms) | speech end → tool call | GLM first token | GLM total | GLM done → first audio |
| -------- | ---- | -------------------------- | ---------------------- | --------------- | --------- | ---------------------- |
|          |      |                            |                        |                 |           |                        |

Not logged. The owner's verdict in the thread: mode C "works pretty good".

## Findings

1. **Gemini as the voice of another brain has a structural flaw.** The Live
   API cannot force a function call, so in mode B Gemini answers some
   questions itself with its own knowledge and personality. Mode C narrows it
   with a prompt but cannot close it, and Gemini still paraphrases.
2. **Privacy floor with Gemini Live.** Google receives all speech and, since
   it must speak them, all of the agent's answers. Paid tier: not used for
   training, logged 55 days for abuse monitoring, no zero-retention or region
   option. Only the local stack (mode D) keeps audio off Google.
3. **The dominant latency in mode D is the brain, not the local stack.**
   Whisper is under 200 ms, Kokoro 200 to 400 ms per sentence; GLM's first
   token is 0.3 to 1.9 s depending on OpenRouter's mood, and the first
   sentence must complete before it can be spoken. Turn detection adds a
   fixed 500 ms silence window.
4. **Turn detection decides the experience.** An energy-threshold VAD in the
   browser made mode D unusable in a normal room; Silero on the server fixed
   it. GLM's thinking, left on, doubled or tripled the wait.
5. **Mode D end to end, owner's session: 1.6 to 2.5 s from last voice to
   first audio**, of which 0.5 s is the silence window and most of the rest
   is OpenRouter. A local brain (`AGENT_BASE_URL`) would remove the network
   part and is the next thing to measure.

## Expected shape of the result (written before measuring)

Stage costs that cannot be avoided in the mode B design:

- Gemini turn detection (its end-of-speech silence window, default around 800 ms)
  plus the model deciding to call `ask_agent`: paid in both modes, but in
  mode B the model emits a function call instead of audio.
- GLM: time to first token (model think time) and total generation time. The
  function result can only be returned when GLM is done, because the Live API
  function response carries the complete answer. **This is the stage expected
  to dominate**; `glm_total` is the number to watch.
- Gemini reading the function result and starting to speak: the
  "GLM done → first audio" column.

Mode D pays Whisper once per turn and Kokoro once per sentence, but it starts
speaking after GLM's first sentence instead of its last token, so for long
answers it should beat mode C by most of "GLM total" minus "GLM first token".
Measured on this M5 Pro, warm: Whisper turbo about 0.2 s for a short
utterance, Kokoro about 0.3 s per sentence.

If "speech end → playback" in mode B is not acceptable in conversation, the
next experiment named in the brief applies: stream GLM's tokens into
sentence-chunked TTS and drop the second reasoning model.
