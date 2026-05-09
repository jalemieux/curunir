---
name: comfyui-model-seed-hunt
description: "Use when the user wants to find new base model characters — generate SFW/NSFW front-view reference images with random seeds to discover good likenesses for LoRA training. Trigger: 'find model seeds', 'generate model references', 'hunt for models', 'find base characters', 'create reference front views', 'I need a new model to train on', or any request to generate random front-view character sheets for model discovery."
tools: attach
---

# Model Seed Hunting

Generate front-view character reference images with **randomized seeds** using the persephoneFlux NSFW/SFW model. The goal is to **discover good base model likenesses** — faces and bodies that look natural and could serve as LoRA training references. Once you find a winner, you hand it off to the `comfyui-seed-hunt` skill for multi-view character sheet generation.

**Depends on:** the `comfyui` skill for the actual ComfyUI driver. Load `comfyui` first if not already loaded. For prompt authoring, also load `flux2-prompt`.

## What model seed hunting is

Unlike seed-hunting (where you sweep specific seeds with reference images), model seed hunting is **discovery mode**. You generate many images with random seeds from scratch, looking for faces and bodies that happen to look great. Each random seed produces a completely different person. When you find one you like, that image becomes the reference for a full character sheet workflow (front/side/back views via the `comfyui-seed-hunt` skill).

Think of it as: **model seed hunt → discover a person → seed hunt with that person → multi-view character sheet**.

## Workflow

### 1. Ask the user what kind of model they want

Before generating anything, ask the user to describe the character they're looking for. Gather:

| Attribute | Example values | Why it matters |
|-----------|---------------|----------------|
| **Gender** | woman, man, non-binary | Subject pronouns, body type |
| **Ethnicity / ancestry** | East Asian, Black, Latina, Scandinavian, mixed, etc. | Skin tone, facial features |
| **Age range** | early 20s, late 20s, 30s, etc. | Face structure, skin texture |
| **Physique / build** | athletic, slim, curvy, muscular, petite, tall | Body proportions |
| **Hair** | long black, short blonde, curly, braided, etc. | Visual identity |
| **Eye color** | brown, blue, green, hazel | Detail for likeness |
| **Skin tone** | fair, olive, sun-kissed, deep brown, etc. | Rendering consistency |
| **Distinguishing features** | freckles, tattoos, piercing, scars, etc. | Uniqueness for LoRA |
| **SFW or NSFW** | sfw, nsfw, or both | Wardrobe / nudity level |
| **How many images** | 10, 20, 30 | Batch size for discovery |

**Ask concisely** — one message covering the main gaps. If the user already provided most of this, just confirm and proceed. Don't over-interrogate; fill in tasteful defaults for anything missing.

### 2. Author the prompt

Use the `flux2-prompt` skill's rules to write a detailed front-view character sheet prompt. The prompt **must** enforce:

- **Front view, facing camera** — these will become reference images
- **Full body, head to toe, feet visible** — needed for character sheets
- **Neutral, symmetrical pose** — arms at sides or slightly away from body
- **Plain white or light grey background** — clean compositing later
- **Studio lighting** — even, no harsh shadows
- **Wide angle, full-length framing** — capture the whole figure

**Prompt structure** (adapt to user's specifications):

```
A full-body character sheet, front view, facing camera, wide angle full-length
shot from head to toe, feet visible standing on a plain floor. A hyper-realistic
[AGE] [GENDER] with [ETHNICITY FEATURES] and [EYE DESCRIPTION]. [PHYSIQUE
DESCRIPTION — muscles, curves, proportions]. [SKIN TONE] skin with visible
pores. Standing in a neutral, symmetrical pose, centered in frame, arms relaxed
at sides. [NSFW: Full anatomical nudity, detailed muscular and pelvic anatomy. /
SFW: Wearing simple [OUTFIT].] [DISTINGUISHING FEATURES]. Plain white
background, bright even studio lighting. Shot on 35mm lens, wide field of view.
```

**NSFW vs SFW prompt differences:**
- **NSFW:** Include `Full anatomical nudity, detailed muscular and pelvic anatomy.` Remove any clothing references.
- **SFW:** Specify simple clothing (e.g., `Wearing a simple black sports bra and matching shorts.` or `Wearing plain white underwear.`)

### 3. Pre-flight

Confirm ComfyUI is running and the required model is available:

```bash
python skills/comfyui/comfy.py models
# Check for: persephoneFluxNSFWSFW_20FP16.safetensors in checkpoints
```

### 4. Create session and submit batch

```bash
TS=$(date +%Y%m%d-%H%M%S)
SESSION=context/uploads/comfyui/sessions/$TS
mkdir -p "$SESSION/outputs"

cp skills/comfyui/templates/persephone-flux-model-seed.json "$SESSION/workflow.json"
```

Edit the workflow — set the prompt and randomize the seed:

- Node `6.inputs.text` → the authored prompt
- Node `25.inputs.noise_seed` → `0` (randomize)
- Node `9.inputs.filename_prefix` → `model-seed/{label}` (e.g., `model-seed/athletic-asian-f`)
- Node `27.inputs.width` → `768` (fast), `1024` (normal)
- Node `27.inputs.height` → `1152` (fast), `1536` (normal)

**Batch submission script** — generate N images with random seeds:

```python
#!/usr/bin/env python3
"""Model seed discovery: submit N front-view images with random seeds."""
import json, subprocess, sys
from copy import deepcopy
from pathlib import Path

TEMPLATE = Path(__file__).parent / "workflow.json"
COMFYUI = Path("skills/comfyui/comfy.py")

# ── Configuration ──────────────────────────────────────────
COUNT = 20                # number of images to generate
WIDTH = 768               # 768 fast, 1024 normal
HEIGHT = 1152             # 1152 fast, 1536 normal
OUTPUT_PREFIX = "model-seed/athletic-woman"  # organize by model type
# ───────────────────────────────────────────────────────────

template = json.loads(TEMPLATE.read_text())
# Ensure seed is 0 (random) — ComfyUI randomizes on each submit
template["25"]["inputs"]["noise_seed"] = 0
template["27"]["inputs"]["width"] = WIDTH
template["27"]["inputs"]["height"] = HEIGHT

submitted = []
for i in range(COUNT):
    wf = deepcopy(template)
    wf["9"]["inputs"]["filename_prefix"] = f"{OUTPUT_PREFIX}/{i+1:03d}"

    tmp = TEMPLATE.parent / f"tmp_model_{i:03d}.json"
    tmp.write_text(json.dumps(wf))

    result = subprocess.run(
        [sys.executable, str(COMFYUI), "submit", str(tmp)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"FAIL {i+1}: {result.stdout.strip()}", file=sys.stderr)
        continue

    info = json.loads(result.stdout)
    pid = info.get("prompt_id", "?")
    submitted.append({"index": i+1, "prompt_id": pid})
    print(f"OK {i+1:03d}/{COUNT} → {pid}")
    tmp.unlink(missing_ok=True)

out = TEMPLATE.parent / "submitted.json"
out.write_text(json.dumps(submitted, indent=2))
print(f"\nSubmitted {len(submitted)}/{COUNT} jobs.")
```

Save as `$SESSION/model_seed_hunt.py` and run:

```bash
cd /Users/jac/Dev/src/curunir
python "$SESSION/model_seed_hunt.py"
```

### 5. Monitor and deliver

Same queue monitoring as the `comfyui-seed-hunt` skill:

```bash
python skills/comfyui/comfy.py queue

COMFYUI_OUTPUT=$(find /Users -maxdepth 5 -path "*/ComfyUI/output" -type d 2>/dev/null | head -1)
find "$COMFYUI_OUTPUT/model-seed" -name "*.png" -newer "$SESSION/workflow.json" | wc -l
```

Once complete, copy to session and deliver:

```bash
cp -r "$COMFYUI_OUTPUT/model-seed" "$SESSION/outputs/"
```

Present the results to the user — show all images and ask them to **pick their favorite(s)**.

### 6. Handoff to comfyui-seed-hunt

When the user picks a winning image:

1. Copy the chosen image to `context/memory/raw/` with a descriptive name (e.g., `front_m3.png`)
2. Suggest using the `comfyui-seed-hunt` skill with that image as reference
3. Optionally generate a back-view variant first (same prompt but `rear view, facing away from camera`) to get two reference angles

## Quality presets

| Setting | Fast (discovery) | Normal (final picks) |
|---------|-------------------|----------------------|
| Width × Height | 768 × 1152 | 1024 × 1536 |
| Steps | 20 | 32 |
| Sampler | euler | euler |
| Scheduler | beta | beta |
| Guidance | 3.5 | 3.5 |
| ~Time per image (MPS) | 30–60s | 90–120s |

## Key editable fields (node IDs)

| Node | Field | What it controls |
|------|-------|------------------|
| `6` | `text` | Positive prompt |
| `9` | `filename_prefix` | Output folder/filename |
| `16` | `sampler_name` | Sampler (euler) |
| `17` | `steps` | Sampling steps |
| `17` | `scheduler` | Scheduler (beta) |
| `25` | `noise_seed` | Seed (0 = random) |
| `26` | `guidance` | FluxGuidance scale |
| `27` | `width` / `height` | Output dimensions |
| `69` | `unet_name` | Model checkpoint |
| `70` | `clip_name1` / `clip_name2` | CLIP models |

## Tips

- **Random seed (0)** is essential here — each image should be a completely different person
- **Front view only** for discovery — you'll generate other views later with seed-hunt
- **Batch 20–30 images** for a good selection pool; expect 3–5 "good enough" picks per batch
- **Smaller resolution (768×1152)** is fine for discovery — you're evaluating likeness, not final quality
- **Once you find a winner, bump to 1024×1536** and re-generate that specific seed at full quality before using it as reference
- **NSFW models tend to default to certain body types** — if results are too samey, vary the physique description aggressively in the prompt
- **The `comfyui-seed-hunt` skill is the next step** — model seed hunt finds the person, seed-hunt generates their character sheet
