---
name: comfyui-model-upscale
description: "Use when the user has curated/discovered model images and wants to regenerate them at higher resolution — preserving the exact seed, prompt, and settings. Trigger: 'upscale this model', 'make a higher res version', 'regenerate at full resolution', 'bump resolution on these', 'hi-res render of these picks', or when the user wants to re-render curated images at better quality."
tools: attach
---

# Model Image Upscale

Re-render curated model discovery images at **higher resolution and quality** while preserving the exact seed, prompt, and all other settings. ComfyUI embeds the full workflow (including seed, prompt, steps, guidance, model) in every output PNG's metadata — this skill reads that metadata, bumps the resolution, optionally increases steps, and re-submits.

**Depends on:** the `comfyui` skill for the ComfyUI driver. Load `comfyui` first if not already loaded.

## What this does

Model seed hunting produces fast, low-res images for discovery (e.g., 768×1152, 8 steps, 0.5 MP). Once you've picked winners, you want to **re-render them at full quality** (e.g., 1024×1536, 32 steps) — same face, same body, same everything, just bigger and sharper. This skill automates that.

## Workflow

### 1. Get curated images from the user

The user provides image paths — either:
- Individual files: `context/uploads/comfyui/sessions/.../outputs/model-seed/001.png`
- A directory of curated picks
- Images they've manually selected and put somewhere

Ask if they haven't provided them: "Which images do you want to upscale? Give me the file paths or tell me which session/directory."

### 2. Extract metadata from each image

ComfyUI embeds the full prompt workflow in each PNG under the `prompt` key in `Image.info`. Extract it:

```python
#!/usr/bin/env python3
"""Extract ComfyUI prompt metadata from PNG files."""
import json, sys
from pathlib import Path
from PIL import Image

for path in sys.argv[1:]:
    p = Path(path)
    if not p.exists():
        print(f"MISSING: {path}", file=sys.stderr)
        continue
    img = Image.open(p)
    raw = img.info.get("prompt", "")
    if not raw:
        print(f"NO METADATA: {p.name}", file=sys.stderr)
        continue
    data = json.loads(raw)

    # Detect workflow type by structural signature
    is_flux2_klein = (
        "103" in data
        and data.get("9", {}).get("class_type") == "Flux2Scheduler"
    )
    is_persephone = (
        "25" in data
        and data.get("9", {}).get("class_type") == "SaveImage"
        and "27" in data  # EmptySD3LatentImage
    )

    if is_flux2_klein:
        wftype = "flux2-klein-seed-hunt"
        seed = data["3"]["inputs"]["noise_seed"]
        text = data["4"]["inputs"]["text"]
        steps = data["9"]["inputs"]["steps"]
        prefix = data["117"]["inputs"]["filename_prefix"]
        image1 = data.get("148", {}).get("inputs", {}).get("image", "")
        image2 = data.get("169", {}).get("inputs", {}).get("image", "")
    elif is_persephone:
        wftype = "persephone-model-seed"
        seed = data["25"]["inputs"]["noise_seed"]
        text = data["6"]["inputs"]["text"]
        steps = data["17"]["inputs"]["steps"]
        prefix = data["9"]["inputs"]["filename_prefix"]
        image1 = image2 = ""
    else:
        wftype = "unknown"
        seed = text = steps = prefix = ""
        image1 = image2 = ""

    print(json.dumps({
        "file": str(p),
        "workflow_type": wftype,
        "seed": seed,
        "prompt": (text or "")[:200],
        "steps": steps,
        "prefix": prefix,
        "image1": image1,
        "image2": image2,
    }, indent=2))
```

Save this as `$SESSION/extract_meta.py` and run:

```bash
python3 "$SESSION/extract_meta.py" /path/to/image1.png /path/to/image2.png
```

### 3. Pick the right template

Based on the detected `workflow_type`, copy the appropriate template:

| Source workflow | Template to use |
|----------------|-----------------|
| `persephone-model-seed` | `persephone-flux-model-seed.json` |
| `flux2-klein-seed-hunt` | `flux2-klein-seed-hunt.json` |
| `unknown` | Ask the user which template to use |

```bash
cp skills/comfyui/templates/<template>.json "$SESSION/workflow.json"
```

If the curated set mixes both workflow types, run two upscale passes — one per template.

### 4. Settings for high-res

Change only what differs between discovery and final render. Everything else (seed, prompt, sampler, guidance, model, references) stays identical.

**For persephone-flux-model-seed (model seed hunt upscales):**

| Field | Discovery value | Upscale value |
|-------|----------------|---------------|
| `27.inputs.width` | 768 | **1024** |
| `27.inputs.height` | 1152 | **1536** |
| `17.inputs.steps` | 20 | **32** |
| `9.inputs.filename_prefix` | `model-seed/...` | `model-hires/...` |
| `25.inputs.noise_seed` | 0 (random) | **the specific seed** |
| `6.inputs.text` | the prompt | **same prompt** |

**For flux2-klein-seed-hunt (seed hunt upscales):**

| Field | Discovery value | Upscale value |
|-------|----------------|---------------|
| `115.inputs.megapixels` | 0.5 | **1.5** or **2.0** |
| `133.inputs.megapixels` | 0.5 | **1.5** or **2.0** |
| `9.inputs.steps` | 8 | **12** |
| `117.inputs.filename_prefix` | `seedhunt/...` | `seedhunt-hires/...` |
| `3.inputs.noise_seed` | original seed | **same seed** |
| `4.inputs.text` | the prompt | **same prompt** |
| `148.inputs.image` | reference image | **same reference** |
| `169.inputs.image` | reference image | **same reference** |

### 5. Batch submission script

The script dispatches by `workflow_type`, so it only edits fields that exist in the active template. Don't pollute the other template's nodes.

```python
#!/usr/bin/env python3
"""Upscale curated images: re-render at higher resolution, same seed/prompt."""
import json, subprocess, sys
from copy import deepcopy
from pathlib import Path
from PIL import Image

TEMPLATE = Path(__file__).parent / "workflow.json"
COMFYUI = Path("skills/comfyui/comfy.py")

# ── Configuration ──────────────────────────────────────────
WORKFLOW_TYPE = "persephone-model-seed"  # or "flux2-klein-seed-hunt"
IMAGES = [
    "/path/to/curated/pick1.png",
    "/path/to/curated/pick2.png",
]
# Persephone hi-res targets
HIRES_W, HIRES_H, HIRES_STEPS = 1024, 1536, 32
# Flux2-klein hi-res targets
HIRES_MP, HIRES_KLEIN_STEPS = 1.5, 12
# ───────────────────────────────────────────────────────────

template = json.loads(TEMPLATE.read_text())

def read_meta(img_path: Path) -> dict | None:
    img = Image.open(img_path)
    raw = img.info.get("prompt", "")
    return json.loads(raw) if raw else None

def patch_persephone(wf: dict, meta: dict, stem: str) -> None:
    wf["27"]["inputs"]["width"]  = HIRES_W
    wf["27"]["inputs"]["height"] = HIRES_H
    wf["17"]["inputs"]["steps"]  = HIRES_STEPS
    wf["25"]["inputs"]["noise_seed"] = meta["25"]["inputs"]["noise_seed"]
    wf["6"]["inputs"]["text"]    = meta["6"]["inputs"]["text"]
    wf["9"]["inputs"]["filename_prefix"] = f"model-hires/{stem}"

def patch_flux2_klein(wf: dict, meta: dict, stem: str) -> None:
    wf["115"]["inputs"]["megapixels"] = HIRES_MP
    wf["133"]["inputs"]["megapixels"] = HIRES_MP
    wf["9"]["inputs"]["steps"] = HIRES_KLEIN_STEPS
    wf["3"]["inputs"]["noise_seed"] = meta["3"]["inputs"]["noise_seed"]
    wf["4"]["inputs"]["text"]       = meta["4"]["inputs"]["text"]
    wf["148"]["inputs"]["image"]    = meta["148"]["inputs"]["image"]
    wf["169"]["inputs"]["image"]    = meta["169"]["inputs"]["image"]
    wf["117"]["inputs"]["filename_prefix"] = f"seedhunt-hires/{stem}"

PATCHERS = {
    "persephone-model-seed": patch_persephone,
    "flux2-klein-seed-hunt": patch_flux2_klein,
}
patch = PATCHERS[WORKFLOW_TYPE]

submitted = []
for img_path_str in IMAGES:
    img_path = Path(img_path_str)
    if not img_path.exists():
        print(f"SKIP (missing): {img_path}", file=sys.stderr)
        continue

    meta = read_meta(img_path)
    if meta is None:
        print(f"SKIP (no metadata): {img_path.name}", file=sys.stderr)
        continue

    wf = deepcopy(template)
    stem = img_path.stem
    patch(wf, meta, stem)

    tmp = TEMPLATE.parent / f"tmp_hires_{stem}.json"
    tmp.write_text(json.dumps(wf))

    result = subprocess.run(
        [sys.executable, str(COMFYUI), "submit", str(tmp)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"FAIL {stem}: {result.stdout.strip()}", file=sys.stderr)
        continue

    info = json.loads(result.stdout)
    submitted.append({"file": str(img_path), "stem": stem, "prompt_id": info.get("prompt_id", "?")})
    print(f"OK {stem} → {info.get('prompt_id', '?')}")
    tmp.unlink(missing_ok=True)

out = TEMPLATE.parent / "submitted.json"
out.write_text(json.dumps(submitted, indent=2))
print(f"\nSubmitted {len(submitted)}/{len(IMAGES)} upscaled renders.")
```

Run:

```bash
cd /Users/jac/Dev/src/curunir
python "$SESSION/upscale.py"
```

### 6. Monitor and deliver

Same queue monitoring as other skills:

```bash
python skills/comfyui/comfy.py queue

COMFYUI_OUTPUT=$(find /Users -maxdepth 5 -path "*/ComfyUI/output" -type d 2>/dev/null | head -1)
find "$COMFYUI_OUTPUT/model-hires" -name "*.png" -newer "$SESSION/workflow.json" | wc -l
find "$COMFYUI_OUTPUT/seedhunt-hires" -name "*.png" -newer "$SESSION/workflow.json" | wc -l
```

Once done:

```bash
cp -r "$COMFYUI_OUTPUT/model-hires" "$SESSION/outputs/" 2>/dev/null
cp -r "$COMFYUI_OUTPUT/seedhunt-hires" "$SESSION/outputs/" 2>/dev/null
```

Deliver the upscaled images to the user.

## Resolution targets

| Source | Discovery | Upscaled | Notes |
|--------|-----------|----------|-------|
| Persephone (manual) | 768 × 1152 | **1024 × 1536** | ~1.78× pixel count |
| Flux 2 Klein (seed-hunt) | 0.5 MP (~768×640) | **1.5 MP** (~1536×1024) | 3× pixel count |
| Flux 2 Klein (seed-hunt) | 0.5 MP | **2.0 MP** | Max quality, slower |

## Key principle

**Only change resolution and steps. Everything else stays identical** — same seed, same prompt, same model, same guidance, same sampler, same reference images. The seed is what makes it the same person; the prompt is what makes it the same pose; the steps/resolution are what make it higher quality.

## Tips

- **Seed preservation is critical** — always read the seed from the PNG metadata, never guess
- **If the image has no metadata** (e.g., screenshots, re-saved images), ask the user for the seed manually
- **Reference images must exist** — if the original used `front_m2.png` as a reference, that file must still be in ComfyUI's `input/` directory
- **Steps tradeoff** — 32 steps for persephone, 12 for flux2 klein. More steps = slower but better quality
- **Don't mix workflow types in one batch** — run separate upscale passes per template type
- **This is the bridge** between `comfyui-model-seed-hunt` / `comfyui-multiview-seed-hunt` (discovery) and final LoRA training reference images
