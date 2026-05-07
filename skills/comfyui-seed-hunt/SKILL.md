---
name: comfyui-seed-hunt
description: "Use when the user wants to bulk-generate images across many seeds to find good character/pose variations — aka 'seed hunting'. Trigger: 'hunt for seeds', 'generate a bunch of seeds', 'seed hunt', 'sweep seeds from N to N+M', 'try many seeds for this prompt', or any request to systematically vary seeds across multiple prompts."
tools: attach
---

# Seed Hunting

Bulk-generate images across a range of seeds to find the best character likeness, pose, and composition. Uses the `flux2-klein-seed-hunt` ComfyUI template (or any similar bulk-generation template) with 1–2 reference images and one or more prompts.

**Depends on:** the `comfyui` skill for the actual ComfyUI driver. Load `comfyui` first if not already loaded.

## What seed hunting is

Instead of generating one image and tweaking it, you submit a **batch of jobs** that differ only by seed. Each seed produces a different random noise pattern, so the model explores different compositions while keeping the same prompt, reference images, and settings. You then **review the grid** to pick which seeds produce the best results.

Typical use case: training character LoRAs — you need reference images from multiple angles (front, side, back) with consistent likeness, so you sweep 20–40 seeds per angle prompt.

## Workflow

### 1. Gather inputs

The user must provide (or you must have in context):

| Input | Required | Example |
|-------|----------|---------|
| Reference image(s) | Yes (1 or 2) | `context/memory/raw/front_photo.png`, `context/memory/raw/back_photo.png` |
| Prompt(s) | Yes (1+) | Full character sheet prompts for each view |
| Starting seed | Optional (default: random) | `541502447579253` |
| Seeds per prompt | Optional (default: 20) | `20` |
| Output quality | Optional (default: fast) | `fast` or `quality` |

If the user gives reference image **paths**, copy them to ComfyUI's `input/` directory first:

```bash
COMFYUI_INPUT=$(find /Users -maxdepth 5 -path "*/ComfyUI/input" -type d 2>/dev/null | head -1)
cp <path-to-ref-image> "$COMFYUI_INPUT/"
```

### 2. Create the session

```bash
TS=$(date +%Y%m%d-%H%M%S)
SESSION=context/uploads/comfyui/sessions/$TS
mkdir -p "$SESSION/outputs"
```

Copy the seed-hunt template into the session:

```bash
cp skills/comfyui/templates/flux2-klein-seed-hunt.json "$SESSION/workflow.json"
```

### 3. Write the batch script

Create a Python script `$SESSION/seed_hunt.py` that:

1. Loads the template workflow JSON
2. For each (prompt_label, prompt_text) pair × seed range, creates a deep copy
3. Sets the editable fields on each copy
4. Submits all jobs via `comfy.py submit`
5. Saves all prompt_ids for monitoring

Here's the canonical script — adapt it each time:

```python
#!/usr/bin/env python3
"""Bulk seed-hunt: submit N jobs per prompt, sweeping seeds."""
import json, subprocess, sys
from copy import deepcopy
from pathlib import Path

TEMPLATE = Path(__file__).parent / "workflow.json"  # session copy of template
COMFYUI = Path("skills/comfyui/comfy.py")

# ── Configuration ──────────────────────────────────────────
PROMPTS = [
    ("p1_front",  "A full-body character sheet, front view, ..."),
    ("p2_side",   "A full-body character sheet, side view, ..."),
    ("p3_back",   "A full-body character sheet, back view, ..."),
]

START_SEED    = 541502447579253
SEEDS_PER     = 20          # images per prompt
IMAGE1        = "front_photo.png"   # identity reference
IMAGE2        = "front_photo.png"   # composition reference (same if only 1 ref)
OUTPUT_PREFIX = "seedhunt"  # ComfyUI SaveImage prefix

# Quality presets
# fast:    0.5 MP, 8 steps, cfg 4.0
# quality: 0.88 MP, 12 steps, cfg 4.5
MEGAPIXELS = 0.5
STEPS      = 8
CFG        = 4.0
LORA_STRENGTH = 0.0   # 0 = off for seed hunt
# ───────────────────────────────────────────────────────────

template = json.loads(TEMPLATE.read_text())

submitted = []
for label, text in PROMPTS:
    for i in range(SEEDS_PER):
        seed = START_SEED + i
        wf = deepcopy(template)

        # Edit workflow nodes
        wf["3"]["inputs"]["noise_seed"] = seed
        wf["4"]["inputs"]["text"] = text
        wf["9"]["inputs"]["steps"] = STEPS
        wf["165"]["inputs"]["cfg"] = CFG
        wf["167"]["inputs"]["strength_model"] = LORA_STRENGTH
        wf["115"]["inputs"]["megapixels"] = MEGAPIXELS
        wf["133"]["inputs"]["megapixels"] = MEGAPIXELS
        wf["148"]["inputs"]["image"] = IMAGE1
        wf["169"]["inputs"]["image"] = IMAGE2
        wf["117"]["inputs"]["filename_prefix"] = f"{OUTPUT_PREFIX}/{label}/seed-{seed}"

        # Write temp workflow and submit
        tmp = TEMPLATE.parent / f"tmp_{label}_{seed}.json"
        tmp.write_text(json.dumps(wf))

        result = subprocess.run(
            [sys.executable, str(COMFYUI), "submit", str(tmp)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"FAIL {label} seed={seed}: {result.stdout.strip()}", file=sys.stderr)
            continue

        info = json.loads(result.stdout)
        pid = info.get("prompt_id", "?")
        submitted.append({"label": label, "seed": seed, "prompt_id": pid})
        print(f"OK {label} seed={seed} → {pid}")
        tmp.unlink(missing_ok=True)

# Save submitted list for monitoring
out = TEMPLATE.parent / "submitted.json"
out.write_text(json.dumps(submitted, indent=2))
total = len(PROMPTS) * SEEDS_PER
done  = len(submitted)
print(f"\nSubmitted {done}/{total} jobs.")
```

Run it:

```bash
cd /Users/jac/Dev/src/curunir
python "$SESSION/seed_hunt.py"
```

### 4. Monitor the queue

Check queue status and wait for completion:

```bash
python skills/comfyui/comfy.py queue
# → {"running": [[1, "id-r"]], "pending": [[2, "id-p1"], ...]}
```

The queue processes ~1 image per 60–90 seconds on MPS (Apple Silicon). For a batch of 60 images, budget ~90 minutes.

Monitor progress by counting output files:

```bash
COMFYUI_OUTPUT=$(find /Users -maxdepth 5 -path "*/ComfyUI/output" -type d 2>/dev/null | head -1)
find "$COMFYUI_OUTPUT/seedhunt" -name "*.png" -newer "$SESSION/seed_hunt.py" | wc -l
```

If the queue appears stalled:

1. Check `python skills/comfyui/comfy.py queue` — if empty, jobs may have been lost (ComfyUI restart)
2. Check `python skills/comfyui/comfy.py history --limit 5` for recent completions
3. If jobs were lost, re-run `seed_hunt.py` to resubmit
4. If a job is stuck running, cancel it: `python skills/comfyui/comfy.py cancel <prompt_id>`

### 5. Collect and deliver results

Once all jobs complete, copy outputs to the session directory and deliver:

```bash
cp -r "$COMFYUI_OUTPUT/seedhunt" "$SESSION/outputs/"
find "$SESSION/outputs/seedhunt" -name "*.png" | sort
```

Report the results to the user:
- Total images generated
- Images per prompt variant
- File paths organized by prompt label and seed
- Optionally attach a few representative samples

## Quality presets

| Setting | Fast (seed hunt) | Quality (final render) |
|---------|-------------------|------------------------|
| Megapixels | 0.5 | 0.88 |
| Steps | 8 | 12 |
| CFG | 4.0 | 4.5 |
| LoRA strength | 0.0 (off) | 0.3–0.7 (as needed) |
| ~Time per image | 60–90s | 2–4 min |

## Key editable fields (node IDs in flux2-klein-seed-hunt template)

| Node | Field | What it controls |
|------|-------|------------------|
| `3` | `noise_seed` | The random seed — the main variable you sweep |
| `4` | `text` | Positive prompt |
| `6` | `text` | Negative prompt (usually empty) |
| `9` | `steps` | Sampling steps (8 fast, 12 quality) |
| `115` | `megapixels` | Output resolution via MP count |
| `133` | `megapixels` | Second image resolution |
| `148` | `image` | Identity reference image filename |
| `169` | `image` | Composition reference image filename |
| `165` | `cfg` | CFG guidance scale |
| `167` | `strength_model` | LoRA strength (0 = off) |
| `117` | `filename_prefix` | Output folder/filename pattern |

## Tips

- **Seed increment by 1** is the standard sweep — adjacent seeds produce noticeably different results
- **Use a descriptive filename_prefix** like `seedhunt/p1_front/seed-{N}` to keep outputs organized by prompt
- **Start with LoRA strength 0** during seed hunting — you're evaluating base model likeness and pose, not LoRA effect
- **Two reference images** (front + back) can improve consistency across view-angle prompts, but using just one for both slots is fine
- **Queue losses happen** — if ComfyUI restarts mid-batch, jobs disappear. Resubmit the whole batch or just the missing seeds
- **MPS is slow but reliable** — budget ~90s/image on Apple Silicon. GPU would be ~10–15s
