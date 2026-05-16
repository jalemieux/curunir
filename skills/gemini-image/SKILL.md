---
name: gemini-image
description: "Use when the user asks to generate, create, or make an image from a text description. Trigger: a request to produce a picture, illustration, logo, diagram, or any image from a prompt — handled via Gemini 2.5 Flash Image."
---

# Gemini Image

Text-to-image generation via the Gemini 2.5 Flash Image model ("Nano Banana"), called over the REST API. Requires `GEMINI_API_KEY` env var and `curl`/`jq`/`base64`.

The API returns the image as base64 inside the JSON response. Every generation is a **three-step workflow that always ends with `attach`**:

1. **Generate** — POST the prompt, save the JSON response.
2. **Decode** — extract the base64 `inlineData` and `base64 -d` it to a `.png` file.
3. **Attach** — call the `attach` tool with that file so the user can see and download it.

Step 3 is not optional. The user is on a chat channel — they cannot see a filesystem path or base64 text. An image that is generated but not attached has not been delivered. Do not finish the turn until `attach` has been called for the file.

## Usage

**Model:** `gemini-2.5-flash-image` — **Endpoint:** `POST .../models/gemini-2.5-flash-image:generateContent`

### Generate an image

```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent?key=$GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"parts": [{"text": "YOUR IMAGE PROMPT HERE"}]}],
    "generationConfig": {
      "responseModalities": ["TEXT", "IMAGE"],
      "imageConfig": {"aspectRatio": "16:9"}
    }
  }' > /tmp/gemini-image-response.json
```

### Decode the result to a PNG

The response `parts` array holds an optional text part *and* the image part. Select the part with `inlineData`, then base64-decode it:

```bash
jq -r '.candidates[0].content.parts[] | select(.inlineData) | .inlineData.data' \
  /tmp/gemini-image-response.json | base64 -d > /tmp/generated.png
```

### Attach the image to the reply — required final step

Deliver the decoded file with the `attach` tool. Give it a descriptive `name` so the download is meaningful:

```
attach(path="/tmp/generated.png", name="watercolor-fox.png")
```

This is the only way the user actually receives the image. Always do this after a successful decode.

### One-liner (generate + decode in a pipe)

```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent?key=$GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"a watercolor fox asleep on a mossy log, soft morning light"}]}],
       "generationConfig":{"responseModalities":["TEXT","IMAGE"],"imageConfig":{"aspectRatio":"1:1"}}}' \
  | jq -r '.candidates[0].content.parts[] | select(.inlineData) | .inlineData.data' \
  | base64 -d > /tmp/generated.png
```

Then attach it: `attach(path="/tmp/generated.png", name="sleeping-fox.png")`.

### Check for a failed generation

If no image came back (safety block, bad prompt), the decode produces an empty file. Inspect the response before assuming success:

```bash
jq -r '.candidates[0].finishReason // "OK", .promptFeedback // empty, .error.message // empty' \
  /tmp/gemini-image-response.json
```

## Reference

**Aspect ratios** (`imageConfig.aspectRatio`) — default is `1:1` if omitted:

`1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9`

Images render at ~1024px on the base dimension. Each image costs ~1290 output tokens.

## Examples

**Square logo concept:**

```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent?key=$GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"minimalist logo for a coffee roastery: a single coffee bean forming a sunrise, two-color flat design, cream and burnt orange"}]}],
       "generationConfig":{"responseModalities":["TEXT","IMAGE"],"imageConfig":{"aspectRatio":"1:1"}}}' \
  | jq -r '.candidates[0].content.parts[] | select(.inlineData) | .inlineData.data' \
  | base64 -d > /tmp/logo.png
```

Then: `attach(path="/tmp/logo.png", name="roastery-logo.png")`

**Widescreen scene for a blog header:**

```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent?key=$GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"a quiet developer workspace at dusk, warm desk lamp, city lights through the window, cinematic, photorealistic"}]}],
       "generationConfig":{"responseModalities":["TEXT","IMAGE"],"imageConfig":{"aspectRatio":"16:9"}}}' \
  | jq -r '.candidates[0].content.parts[] | select(.inlineData) | .inlineData.data' \
  | base64 -d > /tmp/header.png
```

Then: `attach(path="/tmp/header.png", name="blog-header.png")`

## Tips

- Write descriptive, specific prompts — subject, style, lighting, composition, mood. The model handles natural-language prose well; bullet keyword lists are weaker. For complex or character-driven prompts, the `flux2-prompt` skill's prompt-craft guidance transfers directly.
- The model can return rendered text *in* the image fairly reliably — spell out exact wording in quotes when you want a label or sign.
- Pick the aspect ratio that matches the use (`16:9` headers, `9:16` mobile/story, `1:1` avatars/logos). Don't ask the model to "make it wide" in the prompt — set `imageConfig.aspectRatio`.
- Always `attach` the saved file. The user is on a chat channel and cannot see a path or base64 — the image only reaches them as an attachment.

## Common Mistakes

- **Generating but not attaching** — the most common failure. Saving the PNG to disk and then replying "I created the image at /tmp/..." delivers nothing; the user sees only text. The turn is not done until `attach` has been called with the file. Decode → attach, every time.
- **Forgetting to decode** — `.inlineData.data` is base64, not a URL or a path. It must be piped through `base64 -d` into a file before it is an image.
- **Selecting the wrong part** — the `parts` array often contains a text part alongside the image. `parts[0]` may be text; always `select(.inlineData)` rather than indexing `[0]`.
- **`base64 -d` vs `-D`** — Linux/GNU (the Curunir container) uses `base64 -d`; macOS BSD uses `base64 -D`. The examples assume the Linux container. `base64 --decode` works on both.
- **Wrong config field** — aspect ratio is `generationConfig.imageConfig.aspectRatio`. Not `responseFormat`, not a top-level `imageConfig`, not a phrase in the prompt text.
- **Treating an empty output file as success** — a safety-blocked or rejected prompt returns JSON with `finishReason`/`promptFeedback`/`error` and no `inlineData`. Check the response (see "Check for a failed generation") before attaching a 0-byte file.
- **Reaching for the deprecated SDK** — don't use `google-generativeai` or `@google/generative-ai`. This skill calls the REST API directly via curl, matching the `gemini-search` skill.
