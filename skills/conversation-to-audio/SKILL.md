---
name: conversation-to-audio
description: Use when the user asks to turn the current conversation into audio — phrases like "read this conversation aloud", "make an audio of our chat", "send me an mp3 of this thread". Renders the dialogue as an MP3 attachment via the to_audio tool.
tools: to_audio
hidden: true
---

# Conversation to Audio

Turn the current conversation into a single MP3 attachment.

## Steps

1. **Assemble the transcript.** Walk the visible conversation turn-by-turn and format it as plain prose, one turn per paragraph, prefixed with the speaker label. Use the user's name from `context/identity.md` if available; otherwise use `User` and `Assistant`.

   ```
   User: <message>

   Assistant: <message>
   ```

   Skip tool calls, tool results, and system messages — only user and assistant turns belong in the audio. Strip code blocks down to a one-line summary (e.g. "a short Python snippet was shared") rather than reading them character by character.

2. **Call `to_audio`** with the assembled transcript as `content`. Default filename is fine; override only if the user asked for a specific name.

   ```
   to_audio(
     content="User: ...\n\nAssistant: ...\n\nUser: ...",
     filename="conversation-<YYYY-MM-DD>.mp3"
   )
   ```

   The tool handles the spoken-word rewrite internally (it turns labels into natural narration cues, drops markdown, expands acronyms), so do **not** pre-rewrite the transcript yourself. Pass the raw labeled dialogue.

3. **Reply briefly** confirming the audio is attached. One sentence. The MP3 is delivered as an attachment on this response automatically — do not paste the script back to the user.

## Notes

- If the conversation is very long, the rewrite + TTS calls can take 10–30 seconds. That's expected.
- `to_audio` requires `OPENAI_API_KEY`. If the tool returns an error mentioning the key, surface it verbatim to the user.
- Voice/model defaults come from `TTS_VOICE` / `TTS_MODEL`. Only pass `voice` or `model` if the user explicitly asks for a different one.
