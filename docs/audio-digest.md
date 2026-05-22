# Audio Digest

The `to_audio` tool rewrites text for spoken delivery and synthesizes it into an MP3 attached to the agent's reply. The email channel sends the MP3 alongside the text body — same digest, listenable on the go.

## Enabling it for the daily digest

`to_audio` is opt-in: it only loads when a skill declares it. Add it to your local `context/skills/digest/SKILL.md`:

```yaml
---
name: digest
description: Recurring news digest for the morning brief.
tools: to_audio
---

Compose the daily digest, then call `to_audio` with the digest body as
`content`. Send the digest text and the MP3 attachment in the same reply.
```

Once `tools: to_audio` is present, loading the skill unlocks the tool for the rest of the session.

## Configuration

Set in `.env`:

| Var | Default | Notes |
|-----|---------|-------|
| `OPENAI_API_KEY` | — | Required. Used for TTS. |
| `TTS_MODEL` | `tts-1` | `tts-1-hd` doubles cost, marginal quality bump. |
| `TTS_VOICE` | `alloy` | `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`. |
| `EMAIL_ATTACHMENT_DIR` | `/tmp/attachments` | MP3 lands in `<dir>/audio/`. |

Per-call overrides via tool args: `voice`, `model`, `filename`.

## Cost & latency

A typical 1k-character digest runs ~$0.015 through `tts-1` and ~3 seconds end-to-end. The resulting MP3 is ~500 KB–2 MB, well under Gmail's 25 MB attachment limit.

## Failure handling

If TTS fails (bad key, rate limit, network), the tool returns an error string and leaves the attachment list untouched. The text digest still goes out — only the audio is missing.
