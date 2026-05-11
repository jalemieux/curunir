---
name: comfyui-character-pipeline
description: "Sub-flow of the `comfyui` skill. Read this file with the `read` tool when the user wants to generate a LoRA-ready character sheet from a single reference image using Flux Kontext. Covers: multi-angle generation, expression/pose/clothing/lighting diversity, batch workflows with per-pose seed variants, sheet stitching, and training-set curation. Triggers: 'build me a character sheet', 'generate LoRA training data', 'character sheet from one photo', 'kontext character pipeline', 'LoRA training images'."
tools: attach
---

# ComfyUI Character Pipeline (Kontext-based)

Generate a **LoRA-ready character sheet** from a single source image using local Flux Kontext in-context editing.

## Why Kontext instead of seed-hunting

The old pipeline (persephone seed-hunt → multi-view seed-hunt → upscale) required hours of compute and careful seed locking. Kontext eliminates all of that:

| Old pipeline | Kontext pipeline |
|---|---|
| Discover base model via seed hunt (~30 images) | One good reference image |
| Lock front + back refs at high res | Skip — Kontext preserves identity from reference |
| Multi-view seed hunt per pose (5 seeds × N poses) | One Kontext edit per pose, identity preserved |
| Upscale winners | Native 1024×1024 — no upscale needed |
| ~1-2 hours total | ~25-40 min for a full sheet |

**Key insight:** Kontext's `ReferenceLatent` node feeds the source image's latent directly into conditioning, so the model "sees" the character while applying the edit instruction. Identity preservation comes from the model architecture, not from seed locking.

## The pipeline

```
Stage 1: Prepare          Stage 2: Generate          Stage 3: Curate
─────────────────         ─────────────────          ────────────────
Source image              Batch Kontext edits        User picks
→ verify quality          (N poses × K variants)     → LoRA training set
→ copy to ComfyUI input   → stitch preview sheet     → caption (Stage 4)
→ pre-flight models       → user reviews             → train LoRA
```

## Template

The workflow template is `skills/comfyui/templates/flux-kontext-edit.json`.

**Required models:**
- `flux1-kontext-dev.safetensors` (in `models/unet/`)
- `ae.safetensors` (in `models/vae/`)
- `t5xxl_fp16.safetensors` + `clip_l.safetensors` (in `models/clip/`)

## Stage 1 — Prepare

### Pre-flight

```bash
python skills/comfyui/comfy.py models
# Verify: flux1-kontext-dev.safetensors in checkpoints
#          ae.safetensors in vaes
python skills/comfyui/comfy.py nodes --required FluxKontextImageScale,ReferenceLatent,LoadImage
```

### Source image

The user provides one front-facing reference image. Requirements:
- Clear face, good lighting
- Full body or at least upper body visible
- Neutral or simple background (helps Kontext focus on identity)
- 1024×1024 or larger preferred

Copy it to ComfyUI's input directory:
```bash
cp /path/to/source.png /Users/jac/Dev/src/ComfyUI/input/<name>.png
```

### Session setup

```bash
TS=$(date +%Y%m%d-%H%M%S)
PIPELINE=context/uploads/comfyui/sessions/$TS
mkdir -p "$PIPELINE"/{outputs,character-sheet}
```

### Pose list

The standard character sheet for LoRA training covers these categories:

**Multi-angle (6 views):**
1. Front view (source or re-rendered)
2. Profile left
3. Profile right
4. Three-quarter left
5. Three-quarter right
6. Back view

**Poses (4-6):**
7. Seated (on stool)
8. Arms raised
9. Walking / mid-stride
10. Hands on hips
11. Crouching
12. Leaning against wall

**Expressions (3-4):**
13. Smiling warmly
14. Laughing
15. Serious / intense
16. Surprised

**Environments (3-4):**
17. Outdoor park / nature
18. Café / indoor
19. City street / urban
20. Moody / dramatic lighting

**Clothing (2-3):**
21. Casual jeans + t-shirt
22. Black dress / formal
23. Sporty / athletic

**Lighting (2-3):**
24. Golden hour / warm backlight
25. Dramatic side lighting
26. Soft overcast

Total: ~25-30 poses. The user can customize this list — add, remove, or replace entries.

## Stage 2 — Generate

### Per-pose variants

For each pose, generate **4-5 seed variants** so the user can pick the best one. This addresses the stochastic nature of diffusion — same prompt, different seeds give different results. Some will have better identity preservation, some better pose accuracy, some better composition.

Create a workflow per variant:

```python
for pose in pose_list:        # ~25 poses
    for variant in range(5):  # 5 variants per pose
        # copy flux-kontext-edit.json template
        # edit 41.inputs.image → source filename
        # edit 6.inputs.text → pose-specific edit instruction
        # edit 25.inputs.noise_seed → 0 (randomize each)
        # edit 9.inputs.filename_prefix → "{pose_name}_v{variant+1}"
        # save as wf_{pose_name}_v{variant+1}.json
```

### Prompt engineering for Kontext edits

Each pose prompt should follow this pattern:

```
Same [woman/man/person], now [EDIT DESCRIPTION], same [background/clothing/appearance as appropriate], [style/quality notes]
```

**Key rules:**
- Always start with "Same [subject]" — this anchors identity preservation
- Describe the *change*, not the whole scene — Kontext already sees the source
- Preserve what shouldn't change: "same clothing", "same face", "same hair"
- Keep prompts focused — Kontext struggles with too many simultaneous changes

**Example prompts:**

| Pose | Prompt |
|------|--------|
| Profile left | "Same woman in profile view facing left, same white studio background, same clothing and appearance, full body studio photography" |
| Smile | "Same woman smiling warmly at the camera, natural expression, same white studio background, same clothing and appearance, full body studio photography" |
| Café | "Same woman inside a cozy café, warm interior lighting, same clothing and appearance, full body photography" |
| Jeans | "Same woman wearing casual blue jeans and a fitted white t-shirt, standing front view, same white studio background, same face and hair, full body studio photography" |
| Golden hour | "Same woman outdoors at golden hour, warm sunset light, same clothing and appearance, full body photography" |
| Back | "Same woman viewed from behind, back of head and full back visible, same white studio background, same clothing and appearance, full body studio photography" |

### Batch submission and monitoring

```bash
# Submit all workflows
for wf in wf_*.json; do
    python skills/comfyui/comfy.py submit "$wf"
done

# Wait for each (sequential — ComfyUI processes one at a time on MPS)
# ~5 min per image on MPS with flux1-kontext-dev
# Total: ~25 poses × 5 variants × 5 min ≈ 10 hours for full sheet
# Or: ~25 poses × 1 variant × 5 min ≈ 2 hours for quick preview
```

**Practical advice:**
- For a first pass, generate **1 variant per pose** (~25 images, ~2 hours) to get a quick sheet
- Then generate additional variants only for poses where the first result wasn't ideal
- This saves significant compute vs. always generating 5× everything

### Stitch preview sheets

After generation, stitch images into grids for easy review:

```python
from PIL import Image

# Per-category sheets (5 columns)
# Or full sheet: 5 columns × N rows
cols = 5
# Arrange in grid, save as PNG
```

## Stage 3 — Curate

Present the generated images to the user for review. For each category (angles, poses, expressions, etc.), ask them to pick the best variant(s).

### Review criteria

For each image, evaluate:
1. **Identity preservation** — does it still look like the source person?
2. **Pose accuracy** — does it match the requested pose/view?
3. **Quality** — artifacts, blurring, anatomical issues?
4. **Consistency** — does it fit with the other selected images?

### Curated training set

The user's picks form the LoRA training set. Best practices for the final set:
- **20-30 images** minimum for a solid LoRA
- **Balance across categories** — don't over-weight any single pose/angle
- **Regularity images** — consider adding non-character images of similar style to prevent overfitting (the model learning to always generate a white studio)

Save the curated set:
```bash
mkdir -p "$PIPELINE/training-set"
# Copy user's picks from outputs/ to training-set/
```

## Stage 4 — Caption (planned)

Each training image needs a descriptive caption for LoRA training:
- Unique trigger token (e.g., `nikki woman`)
- Pose/body description
- Clothing description
- Expression
- Lighting/environment

This is the remaining gap — the pipeline produces images but not yet captions. Future update will add auto-captioning via vision-language model.

## Estimated wall time on MPS (Apple Silicon)

| Batch size | Per image | Total |
|---|---|---|
| Quick preview (25 poses × 1 variant) | ~5 min | **~2 hours** |
| Full diversity (25 poses × 5 variants) | ~5 min | **~10 hours** |
| Curated re-gen (5 poses × 3 variants) | ~5 min | **~1.25 hours** |

## Tips

- **One good reference is everything.** The quality of the source image directly determines identity preservation across all generated views. Spend time getting this right.
- **Kontext guidance 2.5 is the sweet spot.** Lower (1.5-2.0) gives more creative freedom but risks identity drift. Higher (3.0-4.0) gives stronger prompt adherence but can look rigid.
- **Don't change too many things at once.** "Same woman, now smiling, wearing a red dress, in a forest" will struggle. Keep edits focused: one change (pose OR expression OR clothing OR environment) per prompt.
- **The `FluxKontextImageScale` node is important.** Don't skip it — it optimizes the source image resolution for the Kontext model's latent space.
- **Euler + simple scheduler** is the proven combo for Kontext. Don't swap to beta or other schedulers without testing.
- **ModelSamplingFlux** auto-adjusts shift based on resolution. If you change output dimensions, leave this node in place (or bypass with CTRL-B if you want manual control).
- **Front view re-render** from the source is optional but recommended — it validates the pipeline preserves identity before burning compute on all other poses.
- **When environment/clothing changes drift identity**, tighten the prompt: add "same face, same hair, same person" more explicitly, or try a higher guidance value.
