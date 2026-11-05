# Findings

Status: **not yet measured**. The bot that built this POC has no Gemini or
OpenRouter key in its environment, so the numbers below are to be filled in by
whoever runs it first. The procedure is fixed so both modes are compared on the
same questions.

## Procedure

1. Start the server with keys set, open the page in Chrome with headphones.
2. Mode A: ask each question below, wait for the full reply. Note the
   "speech end → playback" column.
3. Mode B: disconnect, switch mode, connect, ask the same questions.
4. Interrupt Gemini mid-sentence once in each mode and check that no stale
   audio plays after the interruption.
5. Copy the table rows here.

Questions (same set in both modes):

1. "What is the capital of Australia?"
2. "Explain in a few sentences why the sky is blue."
3. "Give me three tips for sleeping better."
4. "What did I ask you first?" (follow-up / context check)

## Measurements

| Question | Mode | speech end → playback (ms) | speech end → tool call | GLM first token | GLM total | GLM done → first audio |
| -------- | ---- | -------------------------- | ---------------------- | --------------- | --------- | ---------------------- |
|          |      |                            |                        |                 |           |                        |

## Expected shape of the result

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

If "speech end → playback" in mode B is not acceptable in conversation, the
next experiment named in the brief applies: stream GLM's tokens into
sentence-chunked TTS and drop the second reasoning model.
