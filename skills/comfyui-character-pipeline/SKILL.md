---
name: comfyui-character-pipeline
description: "Use when the user wants to go end-to-end from nothing to a finished, hi-res multi-view character sheet ready for LoRA training — discover a base model, lock the seed at full quality, generate front/side/back views, then upscale the winners. Trigger: 'build me a character', 'I need a new character sheet', 'full character pipeline', 'go from scratch to character sheet', 'train a new LoRA from zero', or any request that spans discovery → reference → multi-view → final renders."
---

# ComfyUI Character Pipeline

Orchestrate the **four-stage character creation pipeline** from a blank slate to a hi-res multi-view character sheet. Each stage is a separate skill; this skill is the conductor — it sequences them, holds checkpoints between stages so the user can pick winners, and makes sure the artifact from each stage feeds correctly into the next.

**Depends on:** `comfyui` (driver), `comfyui-model-seed-hunt`, `comfyui-model-upscale`, `comfyui-seed-hunt`, and `flux2-prompt` (for prompt authoring). Load these on demand at each stage — do not preload them all at the start.

## The pipeline

```
Stage 1: discover         Stage 2: lock           Stage 3: multi-view       Stage 4: finish
─────────────────         ────────────             ─────────────────         ───────────────
comfyui-model-seed-hunt → comfyui-model-upscale → comfyui-seed-hunt      → comfyui-model-upscale
random seeds, low res     fixed seed, full res     side/back from ref       hi-res final views
20–30 throwaway picks     1–3 hero references      20–40 seeds per view     5–10 keepers
```

The artifact handed between stages:

| From → To | Artifact | What changes |
|-----------|----------|--------------|
| 1 → 2 | User-picked discovery PNG(s) | Resolution + steps go up; seed/prompt preserved (read from PNG metadata) |
| 2 → 3 | The hi-res front-view reference | Becomes `IMAGE1`/`IMAGE2` for seed-hunt; new view-angle prompts authored |
| 3 → 4 | User-picked sheet view PNG(s) | Megapixels + steps go up; seed/prompt/refs preserved |

## Workflow

### Stage 0 — Pre-flight (once)

Before starting, confirm ComfyUI is up and the required models are present. This avoids waiting through stage 1 only to find a missing checkpoint.

```bash
python skills/comfyui/comfy.py models
# Required: persephoneFluxNSFWSFW_20FP16.safetensors  (stages 1–2)
#           flux-2-klein-base-9b.safetensors          (stages 3–4)
```

If anything is missing, stop and tell the user the exact filename(s) needed.

Create one **pipeline session directory** that holds artifacts from every stage — keeps things easy to reason about across long-running batches:

```bash
TS=$(date +%Y%m%d-%H%M%S)
PIPELINE=context/uploads/comfyui/pipelines/$TS
mkdir -p "$PIPELINE"/{1-discover,2-reference,3-sheet,4-final}
```

### Stage 1 — Discover (comfyui-model-seed-hunt)

Load the skill and follow it:

```
load_skill("comfyui-model-seed-hunt")
```

Use `$PIPELINE/1-discover` as the session dir for that skill. The output is 20–30 low-res front-view candidates.

**Checkpoint:** present the grid to the user and ask them to **pick 1–3 winners**. Save their picks under `$PIPELINE/1-discover/picks/`.

Don't proceed automatically. The user must approve picks before stage 2 — wrong base character ⇒ everything downstream is wasted compute.

### Stage 2 — Lock the reference (comfyui-model-upscale)

```
load_skill("comfyui-model-upscale")
```

Feed it the picks from stage 1. The skill reads each PNG's embedded seed/prompt and re-renders at full resolution (1024×1536, 32 steps for persephone). Output goes to `$PIPELINE/2-reference/`.

**Checkpoint:** confirm the upscaled reference still looks like the same person — sometimes the higher step count drifts slightly. If drift is unacceptable, go back to stage 1 with a different pick (do **not** change the seed at this stage; the seed *is* the identity).

Then **promote the chosen reference** into ComfyUI's `input/` directory under a stable name (e.g., `front_m4.png`) so stage 3 can refer to it:

```bash
COMFYUI_INPUT=$(find /Users -maxdepth 5 -path "*/ComfyUI/input" -type d 2>/dev/null | head -1)
cp "$PIPELINE/2-reference/<chosen>.png" "$COMFYUI_INPUT/front_m4.png"
```

Optionally generate a back-view variant of the same seed (same prompt, swap "front view, facing camera" → "rear view, facing away from camera") and promote it as `back_m4.png`. Two reference angles improve consistency in stage 3 but one is fine.

### Stage 3 — Multi-view sheet (comfyui-seed-hunt)

```
load_skill("comfyui-seed-hunt")
load_skill("flux2-prompt")   # for authoring the view-angle prompts
```

Use `flux2-prompt` to write 3 prompts — front, side, back — that share identity language with the stage-2 reference (same age/build/skin/hair/wardrobe wording) and only vary the view direction and pose. Hand these to `comfyui-seed-hunt` with `IMAGE1=front_m4.png`, `IMAGE2=back_m4.png` (or just front for both slots), and sweep ~20 seeds per prompt.

Session dir: `$PIPELINE/3-sheet`.

**Checkpoint:** present the per-prompt grids and ask the user to pick **the best 1–3 seeds per view** (so 3–9 keepers total).

### Stage 4 — Hi-res final renders (comfyui-model-upscale)

```
load_skill("comfyui-model-upscale")
```

Feed it the stage-3 picks. Important: this is a **flux2-klein-seed-hunt** workflow type, not persephone — the upscale skill auto-detects from PNG metadata, but if you set `WORKFLOW_TYPE` manually in its script, set it to `"flux2-klein-seed-hunt"` (1.5 MP, 12 steps).

Output: `$PIPELINE/4-final/`. These are the LoRA training reference images.

Deliver the final set to the user with a short summary: how many views × how many seeds per view, file paths, and the original discovery seed (so they can recreate the character later).

## Decision points (don't skip these)

| Between stages | Question | Why it matters |
|---------------|----------|----------------|
| 1 → 2 | "Which 1–3 of these do you want to lock in?" | Identity is fixed once you commit; later stages can't change the face |
| 2 → 3 | "Does the upscaled version still look like the same person?" | Drift here means stage 3 produces views of someone else |
| 3 → 4 | "Which seeds per view do you want at full quality?" | Stage 4 burns ~3× the compute — only upscale keepers |

These are user calls, not your calls. Don't auto-pick.

## Estimated wall time on MPS (Apple Silicon)

| Stage | Volume | Per image | Total |
|-------|--------|-----------|-------|
| 1 | 20 × 0.5 MP, 20 steps | ~45s | ~15 min |
| 2 | 1–3 × 1.78 MP, 32 steps | ~110s | ~5 min |
| 3 | 60 × 0.5 MP, 8 steps | ~75s | ~75 min |
| 4 | 5–10 × 1.5 MP, 12 steps | ~3 min | ~15–30 min |
| **Total** | — | — | **~2 hours** |

Tell the user this up front when they kick off the pipeline. Stage 3 dominates — they should not sit and wait.

## Tips

- **Run the whole pipeline in one session dir** (`$PIPELINE/`) so checkpoints, picks, and final outputs live together. Easier to revisit later than scattered timestamped dirs.
- **Don't skip the upscale at stage 2.** Going straight from low-res discovery to seed-hunt costs you identity fidelity — the reference image quality propagates to every view.
- **The seed is the person.** Across stages 1, 2, and inside any single seed-hunt prompt at stage 3, `noise_seed` is what locks the likeness. Never change it accidentally.
- **Stages 1+2 use persephone, stages 3+4 use flux2-klein.** Different model, different node IDs in the workflow JSON — `comfyui-model-upscale` handles both via its dispatch table, so trust it.
- **Resumability:** if the user pauses between stages, the session dir tells you where you left off — `1-discover/picks/` exists ⇒ stage 1 done, stage 2 not started; `2-reference/` exists ⇒ ready to promote and start stage 3; etc.
- **Failure recovery:** if any sub-skill batch loses jobs to a ComfyUI restart, that sub-skill's monitoring section tells you how to detect and resubmit. This skill doesn't need to know about queue mechanics.
