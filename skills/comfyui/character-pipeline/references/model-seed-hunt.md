---
name: comfyui-model-seed-hunt
description: "Use when the user wants to find new base model characters — generate SFW/NSFW front-view reference images with random seeds to discover good likenesses for LoRA training. Trigger: 'find model seeds', 'generate model references', 'hunt for models', 'find base characters', 'create reference front views', 'I need a new model to train on', or any request to generate random front-view character sheets for model discovery."
tools: attach
---

# Model Seed Hunting

Generate front-view character reference images with **randomized seeds** using the persephoneFlux NSFW/SFW model. The goal is to **discover good base model likenesses** — faces and bodies that look natural and could serve as LoRA training references. Once you find a winner, you hand it off to the `comfyui-multiview-seed-hunt` skill for multi-view character sheet generation.

**Depends on:** the `comfyui` skill for the actual ComfyUI driver. Load `comfyui` first if not already loaded. For prompt authoring, also load `flux2-prompt`.

## What model seed hunting is

Unlike seed-hunting (where you sweep specific seeds with reference images), model seed hunting is **discovery mode**. You generate many images with random seeds from scratch, looking for faces and bodies that happen to look great. Each random seed produces a completely different person. When you find one you like, that image becomes the reference for a full character sheet workflow (front/side/back views via the `comfyui-multiview-seed-hunt` skill).

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
| **Seeds per prompt** | 3, 5, 8 (default 5) | Batch size *per* prompt option — total = 3 × this number |

**Ask concisely** — one message covering the main gaps. If the user already provided most of this, just confirm and proceed. Don't over-interrogate; fill in tasteful defaults for anything missing.

### 2. Draft three prompt options — all three will be run

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

**Always draft three distinct options and run all three** — each gets its own random-seed range (default 5 seeds per prompt → 15 images total). The user is taste-testing both *which prompt direction* lands and *which seeds* within each direction land. Don't collapse to one prompt before submission.

How to make the three variants meaningfully different — vary at least one of:

- **Physique emphasis** — e.g., athletic/lean vs. curvy/soft vs. muscular/powerful
- **Distinguishing features** — e.g., freckles + small chest tattoo vs. clean skin + ear piercings vs. visible scar + nose ring
- **Hair / styling** — e.g., long loose vs. tight braid vs. short cropped (within the user's stated direction)
- **Wardrobe** (SFW only) — e.g., black sports set vs. white underwear vs. neutral tank + briefs
- **Lighting / lens flavor** — e.g., flat studio vs. soft key + fill vs. cooler high-key — kept subtle so background stays clean

Keep all three prompts inside the user's hard constraints (gender, ethnicity, age range, SFW/NSFW). The variation is a structured taste test, not a license to ignore the spec.

**How to present them.** Show the three prompts plainly, labeled `Option A / B / C`, each with a one-line summary of the angle it takes. State the plan: "I'll run all three — N seeds each, M images total." End with:

> Want to tweak any of the three before I submit, or run as-is?

Wait for the user. Do **not** proceed to pre-flight or batch submission until they confirm or revise. If they edit one of the options, treat the edited version as that slot's prompt and re-confirm the full set before submitting.

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

Edit the workflow — the batch script sets these per job:

- Node `6.inputs.text` → one of the three confirmed prompts (per slot)
- Node `25.inputs.noise_seed` → a fresh random 64-bit int per iteration (**don't** use `0` — the API doesn't randomize, only the UI does)
- Node `9.inputs.filename_prefix` → `model-seed/{batch}/{option}/seed-{N}` so prompt option and seed are recoverable from the filename
- Node `27.inputs.width` → `512` (discovery), `1024` (normal)
- Node `27.inputs.height` → `768` (discovery), `1536` (normal)
- Node `17.inputs.steps` → `8` (discovery), `32` (normal)

**Batch submission script** — runs all three confirmed prompts, each with its own random-seed range:

```python
#!/usr/bin/env python3
"""Model seed discovery: submit SEEDS_PER fresh-random-seed images for EACH
of the three confirmed prompt options.

Note: ComfyUI's "randomize seed on generate" is a UI feature. When submitting
via the API, the seed in the workflow is used verbatim — submitting seed=0
N times produces the same image N times. We must generate a distinct seed
per iteration in Python.
"""
import json, secrets, subprocess, sys
from copy import deepcopy
from pathlib import Path

TEMPLATE = Path(__file__).parent / "workflow.json"
COMFYUI = Path("skills/comfyui/comfy.py")

# ── Configuration ──────────────────────────────────────────
# Paste the three user-confirmed prompts here. Labels become folder names.
PROMPTS = [
    ("optA_athletic",   "A full-body character sheet, front view, ..."),
    ("optB_curvy",      "A full-body character sheet, front view, ..."),
    ("optC_muscular",   "A full-body character sheet, front view, ..."),
]

SEEDS_PER     = 5          # random seeds per prompt option (total = 3 × this)
WIDTH         = 512        # 512 discovery, 1024 normal — Flux floor at 2:3 portrait
HEIGHT        = 768        # 768 discovery, 1536 normal
STEPS         = 8          # 8 discovery, 32 normal — fine for likeness triage
BATCH_LABEL   = "athletic-woman"   # top-level folder for this discovery run
SEED_BITS     = 64         # ComfyUI accepts up to 64-bit seeds
# ───────────────────────────────────────────────────────────

template = json.loads(TEMPLATE.read_text())
template["27"]["inputs"]["width"] = WIDTH
template["27"]["inputs"]["height"] = HEIGHT
template["17"]["inputs"]["steps"] = STEPS

submitted = []
for label, text in PROMPTS:
    for i in range(SEEDS_PER):
        seed = secrets.randbits(SEED_BITS)   # fresh seed per iteration
        wf = deepcopy(template)
        wf["6"]["inputs"]["text"] = text
        wf["25"]["inputs"]["noise_seed"] = seed
        wf["9"]["inputs"]["filename_prefix"] = (
            f"model-seed/{BATCH_LABEL}/{label}/{i+1:02d}-seed-{seed}"
        )

        tmp = TEMPLATE.parent / f"tmp_{label}_{i:02d}.json"
        tmp.write_text(json.dumps(wf))

        result = subprocess.run(
            [sys.executable, str(COMFYUI), "submit", str(tmp)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"FAIL {label} #{i+1} seed={seed}: {result.stdout.strip()}",
                  file=sys.stderr)
            continue

        info = json.loads(result.stdout)
        pid = info.get("prompt_id", "?")
        submitted.append({"option": label, "index": i+1, "seed": seed,
                          "prompt_id": pid})
        print(f"OK {label} {i+1:02d}/{SEEDS_PER} seed={seed} → {pid}")
        tmp.unlink(missing_ok=True)

out = TEMPLATE.parent / "submitted.json"
out.write_text(json.dumps(submitted, indent=2))
total = len(PROMPTS) * SEEDS_PER
print(f"\nSubmitted {len(submitted)}/{total} jobs across {len(PROMPTS)} prompts.")
```

Save as `$SESSION/model_seed_hunt.py` and run:

```bash
cd /Users/jac/Dev/src/curunir
python "$SESSION/model_seed_hunt.py"
```

### 5. Monitor and deliver

Same queue monitoring as the `comfyui-multiview-seed-hunt` skill:

```bash
python skills/comfyui/comfy.py queue

COMFYUI_OUTPUT=$(find /Users -maxdepth 5 -path "*/ComfyUI/output" -type d 2>/dev/null | head -1)
find "$COMFYUI_OUTPUT/model-seed" -name "*.png" -newer "$SESSION/workflow.json" | wc -l
```

Once complete, copy to session and deliver:

```bash
cp -r "$COMFYUI_OUTPUT/model-seed" "$SESSION/outputs/"
```

Present the results grouped by prompt option (Option A / B / C), so the user can compare both *which prompt direction* worked and *which seeds* within it landed. Ask them to pick their favorite(s) — the filename carries the seed and option for the handoff step.

### 6. Handoff to comfyui-multiview-seed-hunt

When the user picks a winning image:

1. Copy the chosen image to `context/memory/raw/` with a descriptive name (e.g., `front_m3.png`)
2. Suggest using the `comfyui-multiview-seed-hunt` skill with that image as reference
3. Optionally generate a back-view variant first (same prompt but `rear view, facing away from camera`) to get two reference angles

## Quality presets

| Setting | Fast (discovery) | Normal (final picks) |
|---------|-------------------|----------------------|
| Width × Height | 512 × 768 | 1024 × 1536 |
| Steps | 8 | 32 |
| Sampler | euler | euler |
| Scheduler | beta | beta |
| Guidance | 3.5 | 3.5 |
| ~Time per image (MPS) | 10–15s | 90–120s |

512 × 768 is the practical Flux floor for a 2:3 portrait — multiples of 64,
~0.39 MP, enough resolution to triage likeness without burning compute. Going
lower (e.g., 384 × 576) starts breaking anatomy on Flux. Stage 2 re-renders
the user's pick at 1024 × 1536 / 32 steps, so any softness here is thrown
away in the upscale.

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

- **A fresh random seed per submission** is essential — each image should be a completely different person. The batch script draws a new 64-bit seed per iteration; setting `noise_seed=0` once and submitting N times will give you the same image N times (the API doesn't randomize, only the UI does).
- **Front view only** for discovery — you'll generate other views later with seed-hunt
- **3 prompts × 5 seeds = 15** is the default sweet spot — broad enough to compare prompt directions, small enough to review quickly. Bump `SEEDS_PER` only if all three prompts are clearly viable and you want a deeper pool
- **Smallest Flux-safe resolution (512×768) and 8 steps** is the right discovery preset — you're evaluating likeness, not final quality, and the upscale stage re-renders the winner at 1024×1536 / 32 steps anyway
- **Once you find a winner, bump to 1024×1536 / 32 steps** and re-generate that specific seed at full quality before using it as reference
- **NSFW models tend to default to certain body types** — if results are too samey, vary the physique description aggressively in the prompt
- **The `comfyui-multiview-seed-hunt` skill is the next step** — model seed hunt finds the person, seed-hunt generates their character sheet
