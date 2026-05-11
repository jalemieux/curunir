---
name: comfyui-multiview-seed-hunt
description: "Use when the user has a locked character reference and wants a multi-view character sheet — generate per-pose views anchored to the Stage 2 reference prompt and image. Trigger: 'multi-view character sheet', 'generate front/side/back', 'pose sweep with reference', 'character turnaround seed hunt', or any request that pairs reference images with a pose pack."
tools: attach
---

# Multi-view Seed Hunt

Generate a multi-view character sheet by deriving per-pose prompts from the Stage 2 reference prompt, selecting the right reference image per pose, and sweeping a small number of seeds.

This is the **second seed-hunt** in the character pipeline — after `comfyui-model-seed-hunt` discovers the base character and `comfyui-model-upscale` locks a hi-res reference, this skill generates the per-angle views from that reference.

**Depends on:** `comfyui` (driver — load first if not already loaded).

## What the skill does, in one diagram

```
Inputs                                     Derivation (agent)                    Sweep
──────                                     ─────────────────                    ─────
Stage 2 reference prompt    ───┐
Stage 2 reference seed      ───┤
pose pack (file path)       ───┼──→  swap view clause per pose,     →  5 seeds/pose
front + back ref images     ───┘      pick front or back ref,          via batch script
                                    USER APPROVES BEFORE SWEEP
```

You derive prompts from the Stage 2 reference — you don't compose them from scratch. You don't pick poses — the pose pack does that. Your job is the swap, the ref selection, and the sweep.

## Workflow

### 1. Gather inputs

| Input | Required | Notes |
|---|---|---|
| **Stage 2 reference prompt** | Yes | The exact prompt text used to produce the locked front-view reference in Stage 2. Every pose prompt is derived from this. |
| **Stage 2 reference seed** | Yes | The seed from the locked front-view. Used as the base seed for Stage 3 renders to lock likeness. |
| **Pose pack** | Yes | Path to a pose-pack markdown file. Either a bundled name (resolved against `skills/comfyui/character-pipeline/references/poses/<name>.md`) or an absolute path to a custom pack. |
| **Front reference image** | Yes | Filename in ComfyUI's `input/` directory. |
| **Back reference image** | Yes | Filename in ComfyUI's `input/` directory. |
| **Seeds per pose** | Optional (default 5) | Number of seeds per prompt. 5 is the default; the user said 1 is usually enough but wants 5 as the stated default. |

If the caller didn't name a pose pack, list the bundled options:

```bash
ls skills/comfyui/character-pipeline/references/poses/*.md
```

…and tell the user they can also point at a custom pack file by absolute path. Read each candidate's frontmatter for the `description` and `poses` count to summarize the choice.

### 2. Derive prompts (one per pose)

**This is a derivation step, not a composition step.** You take the Stage 2 reference prompt and swap only the view/positioning clause to match each pose. Everything else — identity blurb, wardrobe, physique, skin, hair, lighting, lens — stays verbatim.

The Stage 2 reference prompt will contain a view/positioning clause like one of:
- `front view, facing camera`
- `wide angle full-length shot from head to toe, feet visible standing on a plain floor. A hyper-realistic... front view, facing camera`
- `rear view, facing away from camera`

**How to derive per-pose prompts:**

1. **Identify the view clause** in the Stage 2 prompt — the part that describes the camera angle and subject orientation. It's usually a short phrase near the start or after the framing clause.
2. **Read the pose pack** — each `### <label>` entry is a simple natural-language instruction describing the pose and camera angle (e.g., "wide angle back view of a woman kneeling on all fours").
3. **For each pose**, create a new prompt by:
   - Taking the Stage 2 prompt verbatim.
   - Replacing the original view clause with the pose instruction, rephrased to match flux2 prompt style (concrete body-position language, no quality tags — see `flux2-prompt` skill for rules).
   - Keep it minimal — the reference image carries most of the identity; the prompt just needs to steer the angle and basic pose.
4. **Don't add anything** that wasn't in the original prompt or the pose instruction. No new wardrobe, no new lighting, no quality tags. Faithfulness > cleverness.

**Pick the reference image per pose:** decide based on the pose's `view` field and overall body orientation:

| Pose orientation | Reference to use |
|---|---|
| Front-facing, 3/4 front, profile facing forward | **Front** ref image |
| Rear-facing, 3/4 back, profile facing backward | **Back** ref image |
| Side profile (ambiguous direction) | **Front** ref (safer default) |

This is a judgment call — the agent decides based on the pose description. The key principle is: use the ref that shows the anatomy the pose will expose. If the pose shows the back of the head / spine / glutes, use the back ref. Otherwise, use the front ref.

Output shape: a list of `(label, prompt_text, ref_image)` triples, one per pose.

**Show the user the derived prompts and wait for approval before submitting.** Format them as labeled blocks, each showing the pose label, which ref image is used, and the full derived prompt. End with:

> Want to tweak any of these before I kick off the sweep, or run as-is?

If the user edits a prompt, take the edited version verbatim for that pose's slot and re-confirm the full set. Don't proceed to the sweep until they confirm.

### 3. Create the session

```bash
TS=$(date +%Y%m%d-%H%M%S)
SESSION=context/uploads/comfyui/sessions/$TS
mkdir -p "$SESSION/outputs"
cp skills/comfyui/templates/flux2-klein-seed-hunt.json "$SESSION/workflow.json"
```

### 4. Write the batch script

Drop the approved `(label, prompt_text, ref_image)` list into the script as `POSES`. The script loops over `POSES × SEEDS_PER`, sets the editable workflow fields per job, and submits via `comfy.py`.

**Single-image reference mode:** each job uses only one reference image. Set `max_images_allowed` to `"1"` on the `ConditioningAddImageReferenceDual` node (node `164`) so the second image slot is ignored. The `IMAGE2` / node `169` LoadImage still needs a valid filename on disk (it's a required input on the node), but it won't be processed.

```python
#!/usr/bin/env python3
"""Multi-view seed hunt: submit SEEDS_PER jobs per pose prompt.
Uses single-image reference — front or back ref selected per pose."""
import json, subprocess, sys
from copy import deepcopy
from pathlib import Path

TEMPLATE = Path(__file__).parent / "workflow.json"
COMFYUI = Path("skills/comfyui/comfy.py")

# ── Configuration ──────────────────────────────────────────
# Paste the user-approved per-pose data here.
# (label, prompt_text, ref_image_filename)
POSES = [
    ("side-left",  "<derived prompt for left side view>",  "front_m4.png"),
    ("side-right", "<derived prompt for right side view>", "front_m4.png"),
]

PACK_NAME     = "turnaround"          # for the output filename layout
BASE_SEED     = 541502447579253       # Stage 2 reference seed — increments per job
SEEDS_PER     = 5                     # seeds per pose (default 5)

# Quality presets
# fast:    0.5 MP, 8 steps, cfg 4.0
MEGAPIXELS    = 0.5
STEPS         = 8
CFG           = 4.0
LORA_STRENGTH = 0.0   # 0 = off for seed hunt
# ───────────────────────────────────────────────────────────

template = json.loads(TEMPLATE.read_text())

submitted = []
for label, text, ref_image in POSES:
    for i in range(SEEDS_PER):
        seed = BASE_SEED + i
        wf = deepcopy(template)
        wf["3"]["inputs"]["noise_seed"]            = seed
        wf["4"]["inputs"]["text"]                  = text
        wf["9"]["inputs"]["steps"]                 = STEPS
        wf["164"]["inputs"]["max_images_allowed"]  = "1"   # single-image ref mode
        wf["165"]["inputs"]["cfg"]                 = CFG
        wf["167"]["inputs"]["strength_model"]      = LORA_STRENGTH
        wf["115"]["inputs"]["megapixels"]          = MEGAPIXELS
        wf["148"]["inputs"]["image"]               = ref_image  # the selected ref
        # node 169 still has its default image — it won't be processed
        wf["117"]["inputs"]["filename_prefix"]     = (
            f"seedhunt/{PACK_NAME}/{label}/seed-{seed}"
        )

        tmp = TEMPLATE.parent / f"tmp_{label}_{seed}.json"
        tmp.write_text(json.dumps(wf))

        result = subprocess.run(
            [sys.executable, str(COMFYUI), "submit", str(tmp)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"FAIL {label} seed={seed}: {result.stdout.strip()}", file=sys.stderr)
            continue

        info = json.loads(result.stdout)
        pid = info.get("prompt_id", "?")
        submitted.append({"label": label, "seed": seed, "ref": ref_image, "prompt_id": pid})
        print(f"OK {label} seed={seed} ref={ref_image} → {pid}")
        tmp.unlink(missing_ok=True)

out = TEMPLATE.parent / "submitted.json"
out.write_text(json.dumps(submitted, indent=2))
total = len(POSES) * SEEDS_PER
print(f"\nSubmitted {len(submitted)}/{total} jobs across {len(POSES)} poses.")
```

Save as `$SESSION/seed_hunt.py` and run:

```bash
cd /Users/jac/Dev/src/curunir
python "$SESSION/seed_hunt.py"
```

### 5. Monitor the queue

```bash
python skills/comfyui/comfy.py queue
# → {"running": [[1, "id-r"]], "pending": [[2, "id-p1"], ...]}
```

Queue processes ~1 image per 60–90 seconds on MPS. For 10 images (2 poses × 5 seeds), budget ~12 minutes.

```bash
COMFYUI_OUTPUT=$(find /Users -maxdepth 5 -path "*/ComfyUI/output" -type d 2>/dev/null | head -1)
find "$COMFYUI_OUTPUT/seedhunt" -name "*.png" -newer "$SESSION/seed_hunt.py" | wc -l
```

If the queue appears stalled:

1. Check `python skills/comfyui/comfy.py queue` — if empty, jobs may have been lost (ComfyUI restart).
2. Check `python skills/comfyui/comfy.py history --limit 5` for recent completions.
3. If jobs were lost, re-run `seed_hunt.py` to resubmit.
4. If a job is stuck running, cancel it: `python skills/comfyui/comfy.py cancel <prompt_id>`.

### 6. Collect and deliver

```bash
cp -r "$COMFYUI_OUTPUT/seedhunt" "$SESSION/outputs/"
find "$SESSION/outputs/seedhunt" -name "*.png" | sort
```

Present the results grouped by pose label so the user can pick the best seed per pose. File paths carry pose label and seed for easy handoff to Stage 4 (model-upscale).

## Quality presets

| Setting | Fast (seed hunt) | Quality (final render) |
|---|---|---|
| Megapixels | 0.5 | 0.88 |
| Steps | 8 | 12 |
| CFG | 4.0 | 4.5 |
| LoRA strength | 0.0 (off) | 0.3–0.7 (as needed) |
| ~Time per image (MPS) | 60–90s | 2–4 min |

## Key editable fields (flux2-klein-seed-hunt template node IDs)

| Node | Field | What it controls |
|---|---|---|
| `3` | `noise_seed` | The random seed — the main variable you sweep |
| `4` | `text` | Positive prompt |
| `6` | `text` | Negative prompt (usually empty) |
| `9` | `steps` | Sampling steps (8 fast, 12 quality) |
| `115` | `megapixels` | IMAGE1 resolution |
| `148` | `image` | **Active** reference image (front or back) |
| `164` | `max_images_allowed` | Set to `"1"` for single-image ref mode |
| `165` | `cfg` | CFG guidance scale |
| `167` | `strength_model` | LoRA strength (0 = off) |
| `117` | `filename_prefix` | Output folder/filename pattern |
| `133` | `megapixels` | IMAGE2 resolution (not processed when max_images_allowed=1) |
| `169` | `image` | IMAGE2 filename (not processed when max_images_allowed=1, but must be a valid file on disk) |

## Tips

- **Prompts are derived, not composed.** Take the Stage 2 reference prompt and swap only the view/positioning clause. Everything else stays verbatim. This is what keeps identity consistent across all views.
- **Reference selection is an agent judgment call.** Use front ref for poses that show the front/sides of the body; back ref for poses that expose the spine/glutes/heels. When in doubt, front ref is the safer default.
- **Single-image reference mode** (`max_images_allowed="1"`) uses only IMAGE1. Node 169 still needs a valid filename but it won't be processed.
- **User approval gate before submit.** Always show the derived prompts and wait. A quick edit to a pose prompt is cheap; re-running a sweep is not.
- **Seed starts from the Stage 2 reference seed** and increments by 1 per job. The reference seed is the anchor — nearby seeds produce related but different results.
- **Start with LoRA strength 0** during seed hunting — evaluating base model likeness and pose, not LoRA effect.
- **Queue losses happen** — if ComfyUI restarts mid-batch, jobs disappear. Resubmit the whole batch or just the missing seeds.
- **MPS is slow but reliable** — budget ~90s/image on Apple Silicon.
