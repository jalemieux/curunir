---
name: flux2-prompt
description: "Use when the user wants a FLUX.2 image-generation prompt — portraits, fashion shots, full-body, cinematic stills, character-consistent series, or any people/scene image to be rendered by FLUX.2 (pro/max/flex/dev/klein). Trigger: requests like 'write me a flux prompt for…', 'make a flux2 portrait of…', 'I need a prompt to generate…', or any time the next step is to feed a prompt into a FLUX.2 endpoint or workflow."
---

# FLUX.2 Prompt Author

Author production-quality FLUX.2 prompts. The output of this skill is **the prompt itself** (and, when warranted, a JSON-structured variant) — not an image.

This skill encodes the operating rules of the FLUX.2 prompting guide. For depth on any specific topic (klein anatomy tuning, ControlNet settings, multi-reference workflows, full hex-color templates, etc.), read `references/flux2-people-guide.md` in this skill directory.

## The Golden Rule

> **Describe what you want to see, in natural language, with the most important elements first.**

FLUX.2's encoder is a vision-language model (Mistral Small 3.1). It parses subject–verb–object structure, preserves context across clauses, and has real world knowledge. **Keyword salad ("8k masterpiece best quality detailed portrait beautiful") is counterproductive** — it wastes tokens and can degrade the result. Quality is the default; ask for *specificity*, not *quality*.

## Priority Order (every prompt follows this)

```
Main subject → Key action → Critical style → Essential context → Secondary details
```

Earlier = more visual weight. Use this both to structure prompts and to control layout (e.g., name the subject before the background to make the subject prominent).

## Length Guide

| Length | Words | Use case |
|---|---|---|
| Short | 10–30 | Exploration / mood-board / style test |
| **Medium** | **30–80** | **Default for almost every people shot** |
| Long | 80+ | Multi-character scenes, complex editorials |

Default to **medium**. Resist the urge to keep adding adjectives — one descriptor per attribute is enough.

## Hard rules — what NEVER goes into a FLUX.2 prompt

- Quality tags: `8k, masterpiece, best quality, highly detailed, 4k, award-winning`
- Redundant style prefixes: `photorealistic, realistic photo, hyperrealistic` (already the default)
- Stacked adjectives: `beautiful stunning gorgeous` — pick one
- Negative-as-positive: `not blurry, no extra fingers` — describe what you *want* instead
- "no watermark / no text" — FLUX.2 doesn't add these

## Authoring workflow

When the user asks for a FLUX.2 prompt:

1. **Identify what they're missing.** A good people prompt names: subject (age, build, hair, skin), action/pose, framing/shot type, lighting, location, wardrobe (with colors). If 3+ of those are missing and the brief is vague, ask **one** focused clarifying question covering the biggest gap (usually shot type + mood). Otherwise fill in tasteful defaults and proceed.
2. **Pick a template** from the section below as the skeleton.
3. **Fill in priority order.** Subject first, then action, then style, then context, then secondary details.
4. **Add specificity where it matters** — pose detail for full-body, lighting for portrait, fabric/color for fashion, lens + aperture for cinematic.
5. **Use hex codes for clothing colors** when the user cares about exact colors or you're matching a brand palette. Format: `#1B2A4A dark navy` (hex + name = most reliable).
6. **Output the prompt as a clean, ready-to-paste block.** No commentary inside the block. After the block, give a one-line note on which FLUX.2 variant suits the result (pro/max for highest fidelity, flex for fine-detail/text, dev for local + ControlNet, klein for fast iteration on consumer GPUs).
7. **Offer a JSON-structured version** when (a) the user is building a production pipeline, (b) the scene has multiple subjects, or (c) precise color/style locking matters.

## Variant cheat sheet

| Variant | Use for | Notes |
|---|---|---|
| **pro / max** | Highest-fidelity portraits, final output | No negative prompts — describe positively. |
| **flex** | Fine detail, text on clothing | Exposes steps + guidance; pro-level quality. |
| **dev (32B)** | Local generation + ControlNet | Supports negatives; needs ~18GB VRAM @ 1MP. |
| **klein (9B/4B)** | Fast iteration on a consumer GPU | More anatomy issues — see klein rules below. |

## Aspect ratio quick pick

| Composition | Ratio |
|---|---|
| Headshot / bust | 4:5 or 1:1 |
| Half-body | 3:4 |
| Full body standing | **2:3 or 9:16** (always use a tall ratio for full body) |
| Couple | 3:4 |
| Group (3–5) | 16:9 or 3:2 |
| Cinematic wide with figure | 21:9 or 16:9 |

**Square (1:1) is the riskiest ratio for full-body figures** — recommend against it.

## Pose description framework

Describe poses head-to-toe in this order:

1. Stance (standing / sitting / leaning / crouching / lying)
2. Weight distribution (which leg bears weight)
3. Torso orientation (facing / three-quarter / turned away)
4. Each arm — what it's doing
5. Head + gaze direction + expression
6. Legs / feet

**The hand-on-object trick:** put something in the subject's hand whenever possible. The object constrains the hand shape and is the single biggest improvement to hand quality.

For full-body shots, **always**:
- Use a portrait aspect ratio (9:16 or 2:3)
- Explicitly say "full-body" or "full shot"
- Describe what the subject is standing on (anchors the feet)
- Describe each limb's position (vague → bad anatomy)

## Lighting + lens vocabulary the model understands

- **Lighting:** golden hour, blue hour, overcast, hard noon sun, rembrandt, split light, chiaroscuro, motivated practical, soft window light, rim light from behind, neon
- **Lenses:** 24mm wide / 35mm documentary / 50mm normal / 85mm portrait / 135mm fashion-tele / 200mm sports
- **Apertures:** f/1.4 (creamy bokeh) → f/2.8 (subject isolation) → f/5.6 (group sharp) → f/11 (deep focus)
- **Shutter cues:** "1/1000s freezing the action", "slight motion blur on the hands"
- **Film stocks:** Kodak Portra 400, Kodak Gold 200, CineStill 800T, Fuji Pro 400H, Ilford HP5 (B&W)

## Templates (skeletons — fill the brackets)

### Clean headshot
```
A professional headshot of a [AGE] [GENDER] with [HAIR], [SKIN], and [EXPRESSION]. Shot with an 85mm lens at f/2.0 against a [COLOR] seamless backdrop. Soft even lighting from a large softbox at 45 degrees. Eyes sharp, slight catchlight visible.
```

### Environmental portrait
```
An environmental portrait of [SUBJECT], [AGE], [PHYSICAL DESCRIPTION], [DOING ACTION] at [LOCATION]. Shot with a 50mm lens at f/2.8. [TIME OF DAY] natural light. [BACKGROUND DETAILS]. Shallow depth of field separating the subject from the environment.
```

### Full-body fashion editorial
```
Full-body fashion editorial shot of [SUBJECT], [BUILD], [POSE]. Wearing [DETAILED OUTFIT WITH HEX COLORS]. [LOCATION/BACKDROP]. Shot from a [LOW/EYE/HIGH] angle with a [MM]mm lens at f/[APERTURE]. [LIGHTING]. [STYLING: hair, makeup]. Vogue editorial style, cinematic color grading.
```

### Street style
```
A candid street style photograph of [SUBJECT], walking down [STREET]. Wearing [OUTFIT]. Shot from a slightly low angle with a 35mm lens at f/2.8. [TIME OF DAY], [WEATHER]. Motion blur on passing elements in the background.
```

### Cinematic still
```
A cinematic still from a [GENRE] film. [SUBJECT], [POSE/EXPRESSION], [LOCATION]. [TIME OF DAY/WEATHER]. Shot with an [MM]mm anamorphic lens, f/[APERTURE]. [LIGHTING — e.g., motivated practical, rim light from behind, orange-blue contrast]. Shallow depth of field, slight film grain, letterboxed composition.
```

### Character reference (use as the seed for a multi-reference series)
```
A detailed character portrait of [NAME], [AGE], [HAIR: color/length/texture/style], [EYE COLOR], [SKIN TONE], [BUILD], [DISTINGUISHING FEATURES]. Neutral expression, facing the camera. Even studio lighting against a grey backdrop. No accessories. Shot with a 105mm macro lens at f/5.6 for uniform sharpness across the face.
```

### Couple
```
A photograph of a couple, [PERSON 1] and [PERSON 2], [POSE/INTERACTION]. [LOCATION]. [LIGHTING]. Shot with a 50mm lens at f/2.0. Both faces in focus, background softly blurred. [MOOD].
```

### Klein-friendly standing portrait (simple, symmetrical)
```
A standing portrait of [SUBJECT], [AGE], [BUILD], [HAIR]. Facing the camera with a neutral expression. Arms at sides. Wearing [SIMPLE OUTFIT]. [BACKGROUND]. Even lighting. Shot with an 85mm lens.
```

## JSON-structured prompt (offer when production / multi-subject / exact colors matter)

```json
{
  "subject": {
    "identity": "...",
    "body_type": "...",
    "expression": "...",
    "wardrobe": {
      "clothing": "... in #HEX color-name, fabric",
      "footwear": "...",
      "accessories": "..."
    }
  },
  "pose": "...",
  "environment": {
    "setting": "...",
    "lighting": "...",
    "background_details": "..."
  },
  "camera": {
    "shot_type": "...",
    "angle": "...",
    "lens": "85mm, f/2.0",
    "film_stock": "Kodak Portra 400"
  },
  "style": "...",
  "color_match": "exact"
}
```

For multi-character scenes, use a `characters[]` array with each entry carrying a `position` (e.g., `"foreground left"`, `"midground right"`) and a `description`.

## Klein-specific rules (only matters when the user is on klein)

Klein has notably worse anatomy than the larger variants. When authoring for klein:

- Prefer **standing** poses (sitting/crouching frequently fail)
- Keep poses **near-symmetrical**, arms relaxed at sides or doing simple things
- Recommend **steps: 6–8, CFG: 1.2–1.5, sampler: euler or res_2s, resolution: 1MP**
- Recommend "generate 8, pick 1" — anatomy varies wildly between seeds

For more depth, see `references/flux2-people-guide.md` §15.

## Negative prompts

- **pro / max / flex** do NOT support negatives. Express everything positively.
- **dev / klein** support negatives but only as *creative exclusions* (e.g., `jewelry, watch, necklace` to suppress accessories), not as quality filters. Things like `ugly, deformed, blurry, extra fingers` do nothing — skip them.

## Common fixes (without negatives)

| Problem | Positive-only fix |
|---|---|
| Unwanted accessories | Describe bare wrists/hands/neck explicitly |
| Wrong expression | Describe desired expression in detail |
| Floating figure | Add what they're standing on |
| Feet cut off | Switch to 9:16 or 2:3; explicitly mention feet/ground |
| Clothing morphs across runs | Specify fabric, cut, color (hex), and how it sits on the body |
| Face inconsistent across a series | Generate one clean reference portrait, use multi-reference on subsequent runs |
| Hands look bad | Put an object in the hand; use a closer crop |

## Output format the user expects

1. The prompt, in a fenced block, ready to paste — nothing in it but the prompt.
2. One line on the recommended variant + aspect ratio.
3. Optional: a JSON variant (when warranted) in a second fenced block.
4. Optional: 1–2 sentences of suggested tweaks if they want variations (different seed, alternate lighting, etc.).

Do not lecture the user about the framework — they don't need to see "here's the priority order I followed." Just deliver the prompt.

## When to read the deep guide

Open `references/flux2-people-guide.md` when you need:

- Klein anatomy tuning detail (steps × CFG × sampler matrix)
- ControlNet settings, preprocessor choice, strength ranges
- Full hex-color JSON template with `color_match: "exact"`
- Body-type / age / skin-tone vocabulary lists
- The full troubleshooting cheat sheet
- Multi-reference image workflow specifics
