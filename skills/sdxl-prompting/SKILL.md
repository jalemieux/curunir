---
name: sdxl-prompting
description: "Use when the user wants an SDXL 1.0 text-to-image prompt — photos, portraits, concept art, cinematic stills, or any image to be rendered by an SDXL 1.0 checkpoint (base + refiner). Trigger: 'write me an SDXL prompt for…', 'make an SDXL portrait of…', 'I need a prompt for epicRealismXL / juggernautXL / any SDXL model', or any time the next step is to feed a positive/negative prompt into an SDXL pipeline or the comfyui skill's sdxl-txt2img.json template."
---

# SDXL 1.0 Prompt Author

The output of this skill is **the prompt itself** — a positive prompt, a negative prompt, and (for `CLIPTextEncodeSDXL`) the `text_g` / `text_l` split. Not an image.

Pairs with the `comfyui` skill's `templates/sdxl-txt2img.json` (API format), which exposes:
- `6.inputs.text_g` — OpenCLIP-G prompt (the full description)
- `6.inputs.text_l` — CLIP-L prompt (usually the same text, or a style-only summary)
- `7.inputs.text` — negative prompt
- `11.inputs.stop_at_clip_layer` — CLIP skip, default `-2`

## The core idea

SDXL 1.0 thrives on **descriptive natural language**, not the keyword salad older SD models wanted. Write it like a caption: subject, what they're doing, where, the lighting/mood, the medium/style — flowing into ~75–100 words. Longer than that and the latent space starts to "smear"; shorter is fine for exploration.

It is a **two-stage model** (base + refiner). The refiner sharpens high-frequency detail, so prompts that name concrete textures and surfaces ("micro-pore skin texture", "intricate woven fabric", "whiskers catching the light") give the refiner something to work with. Vague prompts → soft, generic output.

## Prompt structure (follow this flow)

```
[Style trigger] → Subject → Action/Pose → Environment → Lighting/Mood → Style/Medium → concrete detail/texture
```

- **Style trigger** (optional but powerful — see below): `Cinematic photo of…`, `Analog film…`, `Digital art…`, `Photographic…`, `Fantasy art…`
- **Subject** — who/what is the focus (age, build, hair, skin, defining features)
- **Action/Pose** — what they're doing
- **Environment** — where
- **Lighting/Mood** — atmosphere, time of day
- **Style/Medium** — the "look": 35mm film, oil painting, Unreal Engine render, etc.
- **Concrete detail** — textures and surfaces the refiner can sharpen

Earlier words carry more weight, same as a caption — lead with what matters most.

## Direct style triggers (front-load one)

SDXL was trained against named "styles." Starting a prompt with one of these snaps it toward a coherent look instead of generic AI mush:

| Trigger | Use for |
|---|---|
| `Cinematic photo of …` | film-still look, dramatic lighting, color grading |
| `Analog film …` / `Analog photo …` | grain, soft contrast, vintage stock |
| `Photographic …` / `RAW photo of …` | straight-from-camera realism |
| `Digital art …` | painterly/illustrative digital work |
| `Fantasy art …` | concept-art fantasy scenes |
| `Concept art …` | environment/character design boards |

Don't stack more than one. Don't follow it with redundant realism tags (`photorealistic, hyperrealistic`) — the trigger already did that job.

## Style keyword vocabulary

SDXL 1.0 actually understands these — use them in the Style/Medium slot:

| Category | Keywords |
|---|---|
| **Photography** | depth of field, f/1.8, bokeh, grainy film stock, Kodak Portra 400, Fuji Pro 400H, CineStill 800T, wide-angle lens, 85mm lens, macro lens, golden hour |
| **Digital art** | Unreal Engine 5 render, Octane render, volumetric lighting, cel-shaded, sharp edges, ArtStation trending, matte painting |
| **Traditional** | impasto brushstrokes, charcoal sketch, watercolor wash, etching, canvas texture, ink linework, gouache |
| **Cinematic** | anamorphic lens flares, moody shadows, noir aesthetic, high-contrast, teal-and-orange grade, letterboxed |

## Weighting syntax

Parentheses adjust how much attention SDXL pays to a term:

- `(keyword:1.2)` — +20% emphasis
- `(keyword:0.8)` — −20%
- `(keyword)` — ×1.1 per nesting level (legacy A1111 syntax; prefer the explicit number)

**SDXL is sensitive to weights — stay in 0.5–1.5.** Going higher fries the composition. Use weights sparingly, on one or two elements, not every other word.

## Negative prompt

SDXL 1.0 has much better anatomy than SD 1.5, so the negative is a light touch, not a wall of tags. A solid default:

```
text, watermark, signature, blurry, distorted hands, extra fingers, low resolution, cropped, grainy, deformed, jpeg artifacts
```

- Add `cartoonish, illustration, anime, 3d render` only when you specifically want photorealism.
- Add `(worst quality, low quality:1.4)` style boosters only if the checkpoint's docs recommend them — many fine-tunes don't need it.
- Negatives are for *excluding things you don't want*, not a quality cheat code. If output is soft, fix the **positive** prompt (add concrete detail), don't pile onto the negative.

## text_g vs text_l (CLIPTextEncodeSDXL)

SDXL has two text encoders. In the `sdxl-txt2img.json` template node `6`:
- **`text_g`** (OpenCLIP-ViT-bigG) — give it the full descriptive prompt.
- **`text_l`** (CLIP-ViT-L) — usually the same text works fine. Alternatively put a condensed *style/medium summary* here (e.g. `Cinematic photo, golden hour, 85mm, Kodak Portra 400, 8k`) and the full scene in `text_g` — this is a common trick for nudging style without diluting the subject.

When the user just wants "a prompt", give them one block and note "use it for both `text_g` and `text_l`". Only split when they care about style control.

## Default sampler settings (for the template)

These live in `sdxl-txt2img.json` node `3`, but worth knowing when you recommend tweaks:

| Knob | Default | Notes |
|---|---|---|
| steps | 25 | 20–30 typical for base SDXL; Lightning/Turbo fine-tunes want 4–8 |
| cfg | 5.0 | 4–8 for base SDXL; Lightning checkpoints want 1.0–2.5 |
| sampler | `dpmpp_2m` | `dpmpp_2m` / `dpmpp_sde` / `euler_ancestral` all common |
| scheduler | `karras` | pair with the dpmpp samplers |
| size | 1024×1024 | SDXL is trained at ~1MP — use 1024×1024, 1152×896, 896×1152, 1216×832, 832×1216. Going much below 1024 on a side degrades quality. |
| CLIP skip | −2 | common for SDXL photoreal fine-tunes; base SDXL is fine at −1 too |

If the chosen checkpoint name contains `Lightning`, `Turbo`, `LCM`, or `Hyper`, **drop steps to 4–8 and cfg to 1–2.5** — those distilled models break at default settings.

## Examples

### Cinematic photo (realism)
**Positive (text_g):**
```
Cinematic photo of an elderly clockmaker in a dusty Victorian workshop, golden-hour light filtering through grime-covered windows, dust motes dancing in the air, extreme close-up on weathered hands holding a brass gear, deep wrinkles and visible skin texture, intricate clockwork in shallow focus behind, shot on 35mm film, Kodak Portra 400, hyper-detailed, 8k
```
**text_l:** `Cinematic photo, golden hour, 35mm film, Kodak Portra 400, shallow depth of field, 8k`
**Negative:**
```
text, watermark, blurry, distorted hands, extra fingers, low resolution, cropped, deformed, cartoonish, illustration
```

### Concept art (digital)
**Positive (text_g):**
```
Concept art of a colossal derelict starship half-buried in a crimson desert, two tiny explorers in pressure suits walking toward the breached hull, long shadows at sunset, swirling sand, dramatic scale, volumetric god rays, matte painting, Octane render, ArtStation trending, highly detailed, cinematic composition
```
**text_l:** `Concept art, matte painting, Octane render, volumetric lighting, cinematic`
**Negative:**
```
text, watermark, signature, blurry, low resolution, cropped, deformed, lowres
```

### Portrait with a weighted element
**Positive (text_g):**
```
Photographic close-up portrait of a 24-year-old woman with windswept auburn hair, freckles across her nose, (piercing green eyes:1.2), soft natural skin texture with visible pores, neutral expression, standing on a misty cliff at dawn, overcast diffuse light, shallow depth of field, 85mm lens at f/1.8, Fuji Pro 400H, photorealistic, sharp focus on the eyes
```
**text_l:** `Photographic portrait, 85mm f/1.8, Fuji Pro 400H, overcast soft light, sharp eyes`
**Negative:**
```
text, watermark, blurry, distorted hands, extra fingers, mutated, low resolution, cropped, cartoonish, 3d render, plastic skin
```

## Authoring workflow

When the user asks for an SDXL prompt:

1. **Spot the gaps.** A strong prompt names subject, action/pose, environment, lighting, and a medium/style. If 3+ of those are missing and the brief is vague, ask **one** focused question (usually: realism vs. illustration, and what shot type). Otherwise fill tasteful defaults and go.
2. **Pick a style trigger** that matches the target look — front-load it.
3. **Write in structure order**, flowing to ~75–100 words. One descriptor per attribute; don't stack synonyms.
4. **Seed concrete textures** near the end so the refiner has detail to sharpen.
5. **Add at most 1–2 weights** if something needs emphasis, kept in 0.5–1.5.
6. **Write a light negative** — the default list, plus realism-exclusion tags only if going for photoreal.
7. **Output**: the positive prompt in a fenced block, then the negative in a second block. If the pipeline is the `sdxl-txt2img.json` template, also give the `text_l` line. One line on recommended settings (steps/cfg) — and flag it loudly if the checkpoint is a Lightning/Turbo fine-tune.
8. Don't lecture the user about the structure — just hand over the prompt.

## Common Mistakes

- **Keyword salad.** `1girl, masterpiece, best quality, 8k, ultra detailed, beautiful, gorgeous` is SD-1.5 / anime-model habit. SDXL wants sentences. A couple of quality words at the end are fine; a comma-soup *instead of* description is not.
- **Going way over ~100 words.** Long prompts dilute — the model averages everything and the subject loses prominence. Trim to the essentials.
- **Cranking weights past 1.5.** `(red dress:2.0)` doesn't make it redder, it makes it broken. Cap at 1.5; if one weight isn't enough, the word probably needs to move earlier in the prompt instead.
- **Treating the negative as a quality knob.** Dumping `ugly, deformed, bad anatomy, worst quality, lowres, blurry, jpeg, watermark, signature, text, error, missing fingers, extra digit, fewer digits, cropped, …` does little on SDXL 1.0 beyond the basics. Fix the positive prompt instead.
- **Generating below 1024px.** SDXL is trained at ~1MP. 512×512 produces mush and duplication. Use 1024×1024 or one of the SDXL aspect buckets.
- **Default steps/cfg on a Lightning/Turbo checkpoint.** 25 steps @ cfg 5 will burn a 4-step distilled model into noise. Check the checkpoint name; if it's distilled, 4–8 steps @ cfg 1–2.5.
- **Forgetting `text_l`.** With `CLIPTextEncodeSDXL` you must fill both `text_g` and `text_l` — leaving `text_l` empty throws away half the conditioning. Mirror `text_g` or give it a style summary.
