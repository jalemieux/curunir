---
name: comfyui-character-pipeline
description: "Sub-flow of the `comfyui` skill. Read this file with the `read` tool when the user wants to go end-to-end from nothing to a finished, hi-res multi-view character sheet ready for LoRA training — discover a base model, lock front+back references at full quality, generate side / three-quarter views, then upscale the winners. Triggers from inside the `comfyui` skill: 'build me a character', 'I need a new character sheet', 'full character pipeline', 'train a new LoRA from zero', 'find model seeds', 'multi-view character sheet'. NOT auto-loaded — the top-level `comfyui` skill points at this file."
tools: attach
disabled: true
---

# ComfyUI Character Pipeline

Sub-flow of the `comfyui` skill (this file lives at `skills/comfyui/character-pipeline/SKILL.md`).

Orchestrate the **four-stage character creation pipeline** from a blank slate to a hi-res multi-view character sheet. Each stage is a sub-flow documented in this directory's `references/`; this file is the conductor — it sequences them, holds checkpoints between stages so the user can pick winners, and makes sure the artifact from each stage feeds correctly into the next.

**Depends on:** the parent `comfyui` skill (already loaded — this is a sub-flow of it). Also load `flux2-prompt` at Stage 1 for prompt authoring. The four stage sub-flows are reference docs in this skill, **not** separate loadable skills — read them with the `read` tool when you reach each stage:

- `references/model-seed-hunt.md` — Stage 1
- `references/model-upscale.md` — Stages 2 and 4
- `references/multiview-seed-hunt.md` — Stage 3
- `references/poses/` — pose packs for Stage 3

## The pipeline

```
Stage 1: discover         Stage 2: lock           Stage 3: multi-view       Stage 4: finish
─────────────────         ────────────             ─────────────────         ───────────────
model-seed-hunt        →  model-upscale         →  multiview-seed-hunt    →  model-upscale
random seeds, low res     fixed seed, full res     derived prompts, 5 seeds   hi-res final views
20–30 throwaway picks     1–3 hero refs            one ref per pose (agent     1 keeper per pose
                          + back-view variant       picks front or back)
```

The artifact handed between stages:

| From → To | Artifact | What changes |
|-----------|----------|--------------|
| 1 → 2 | User-picked discovery PNG(s) | Resolution + steps go up; seed/prompt preserved (read from PNG metadata). Front-view ref re-rendered + back-view variant generated from the same seed/prompt. |
| 2 → 3 | The hi-res **front** ref, **back** ref, **reference prompt**, and **reference seed** | Prompts derived by swapping the view clause; one ref image selected per pose (agent decides front vs back based on pose angle); `max_images_allowed="1"` for single-image ref mode |
| 3 → 4 | User-picked sheet view PNG(s) | Megapixels + steps go up; seed/prompt/refs preserved |

Front and back are produced as reference images in Stage 2 — together they cover the head-on and rear anatomy that the multi-view sweep won't re-roll. Stage 3 only sweeps in-between angles (sides, three-quarters).

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

#### Pose pack — required input for Stage 3

Stage 3 needs to know **which poses to seed-hunt for** before the
pipeline starts (so the wall-time estimate is accurate and so the user
isn't ambushed by a mid-pipeline question). Resolve this now:

1. **If the user named a pack at kickoff** (e.g., "use turnaround", or
   gave an absolute path), resolve it:
   - A bare name → `skills/comfyui/character-pipeline/references/poses/<name>.md`
   - An absolute path → use as-is
2. **If they didn't**, list the bundled packs and ask. Print:

   ```bash
   ls skills/comfyui/character-pipeline/references/poses/*.md
   ```

   Then for each one show the `name`, `description`, and `poses` count
   from its frontmatter. Tell the user they can also point at a custom
   pack file by absolute path. See
   `skills/comfyui/character-pipeline/references/poses/README.md` for
   the format if they want to author their own.

3. **Validate** the chosen pack: file exists, has frontmatter with
   `name`/`description`/`poses`, and at least one pose entry under
   `## Poses`. Each pose entry should be a simple natural-language
   instruction describing the pose and camera angle. If validation
   fails, stop and tell the user what's wrong — don't paper over it.

   The bundled packs deliberately exclude front and back poses
   because Stage 2 produces those as reference images. If a custom
   pack includes front/back entries, flag it — they'll just duplicate
   the refs.

4. **Freeze it into the session** so the run is self-contained:

   ```bash
   cp <chosen-pack-path> "$PIPELINE/3-sheet/pose-pack.md"
   ```

   Stage 3 reads this frozen copy, not the source pack — so editing
   the source mid-pipeline won't corrupt the run.

### Stage 1 — Discover (model-seed-hunt)

Read the sub-flow doc and follow it:

```
read skills/comfyui/character-pipeline/references/model-seed-hunt.md
```

Use `$PIPELINE/1-discover` as the session dir for that flow. The output is 20–30 low-res front-view candidates.

**Checkpoint:** present the grid to the user and ask them to **pick 1–3 winners**. Save their picks under `$PIPELINE/1-discover/picks/`.

Don't proceed automatically. The user must approve picks before stage 2 — wrong base character ⇒ everything downstream is wasted compute.

### Stage 2 — Lock the references (front **and** back)

```
read skills/comfyui/character-pipeline/references/model-upscale.md
```

Feed it the picks from Stage 1. The flow reads each PNG's embedded seed/prompt and re-renders at full resolution (1024×1536, 32 steps for persephone). Output goes to `$PIPELINE/2-reference/`.

**Both reference angles are required**, not optional. Stage 3's multi-view sweep covers sides and three-quarters; the front and back references are what carry head-on and rear anatomy across to the final character sheet. Skip the back-view ref and you lose all spine / glutes / heels detail in the final set — which is exactly the gap this pipeline exists to close.

For each chosen front-view pick, generate a matching back-view variant by **re-rendering the same seed and prompt with one swap in the prompt text**: replace `front view, facing camera` (or the equivalent head-on framing clause) with `rear view, facing away from camera`. Everything else — seed, identity blurb, lens, lighting — stays verbatim. The seed lock keeps it the same person from the back.

Practically:

1. Run the front-view upscale per `references/model-upscale.md`. Output: `$PIPELINE/2-reference/front_<stem>.png` (1024×1536, 32 steps).
2. For each front upscale, write a sibling job that flips the framing clause to rear view and submits with the same seed. The model-upscale flow's batch script is the right shape — clone the metadata, edit the prompt text, write a new filename prefix (`front-back/back_<stem>`), submit. Output: `$PIPELINE/2-reference/back_<stem>.png`.
3. Verify the back render actually shows the back. Persephone sometimes ignores the rear-view clause if the rest of the prompt is strongly front-loaded — if the back render still shows the face, tighten the rear-view clause (e.g., "rear view, back to camera, face not visible") and re-run that one seed.

**Checkpoint:** confirm both upscaled refs (front + back) still look like the same person — sometimes the higher step count drifts slightly; sometimes the back render diverges in skin tone or hair length. If drift is unacceptable, go back to Stage 1 with a different pick. Do **not** change the seed at this stage; the seed *is* the identity.

Then **promote both refs** into ComfyUI's `input/` directory under stable names so Stage 3 can refer to them:

```bash
COMFYUI_INPUT=$(find /Users -maxdepth 5 -path "*/ComfyUI/input" -type d 2>/dev/null | head -1)
cp "$PIPELINE/2-reference/front_<chosen>.png" "$COMFYUI_INPUT/front_m4.png"
cp "$PIPELINE/2-reference/back_<chosen>.png"  "$COMFYUI_INPUT/back_m4.png"
```

### Stage 3 — Multi-view sheet (multiview-seed-hunt)

```
read skills/comfyui/character-pipeline/references/multiview-seed-hunt.md
```

Hand the multi-view flow everything it needs and let it own the rest. It will derive per-pose prompts from the Stage 2 reference prompt, get user approval on the derived prompts, and run the sweep.

Inputs to pass through:

- **Stage 2 reference prompt** — the exact prompt text used to produce the locked front-view reference. Every pose prompt is derived from this by swapping only the view/positioning clause; the identity blurb, wardrobe, lighting, and lens stay verbatim. Don't paraphrase between stages.
- **Stage 2 reference seed** — carried from the locked front-view. Used as the seed for all Stage 3 renders to lock likeness.
- **Pose pack** — `$PIPELINE/3-sheet/pose-pack.md` (the frozen copy from Stage 0).
- **Reference images** — both front and back from Stage 2. The agent **decides per-pose which reference to use** based on the pose's view angle: front-facing or side poses → front ref; rear-facing poses → back ref. Only one ref image is fed per job (`IMAGE1`), with `max_images_allowed` set to `"1"` so the second slot is ignored (it still requires a file on disk — keep the default in node 169, it won't be processed).
- **Seeds per pose** — default 5.

Session dir: `$PIPELINE/3-sheet`. The multi-view flow creates its own session subdir under that and writes the seed-hunt batch script + outputs there.

**Checkpoint:** the multi-view flow returns the per-pose grids. Present them and ask the user to pick **the best seed per pose**. Total keepers scale with pack size (e.g., 1 pick/pose × 4 poses = 4 keepers for `character-sheet.md`).

### Stage 4 — Hi-res final renders (model-upscale)

```
read skills/comfyui/character-pipeline/references/model-upscale.md
```

Feed it the stage-3 picks. Important: this is a **flux2-klein-seed-hunt** workflow type, not persephone — the upscale flow auto-detects from PNG metadata, but if you set `WORKFLOW_TYPE` manually in its script, set it to `"flux2-klein-seed-hunt"` (1.5 MP, 12 steps).

Output: `$PIPELINE/4-final/`. These are the LoRA training reference images. Don't forget that the locked Stage-2 refs (front + back) are also part of the LoRA training set — copy them alongside:

```bash
cp "$PIPELINE/2-reference/front_<chosen>.png" "$PIPELINE/4-final/"
cp "$PIPELINE/2-reference/back_<chosen>.png"  "$PIPELINE/4-final/"
```

Deliver the final set to the user with a short summary: how many views × how many seeds per view, file paths, and the original discovery seed (so they can recreate the character later).

## Decision points (don't skip these)

| Between stages | Question | Why it matters |
|---------------|----------|----------------|
| 0 → 1 | "Which pose pack should I use for Stage 3?" | The pose set defines what the character is useful for downstream — turnaround for LoRA, character-sheet for game/comic bibles, custom pack for fashion/action/etc. Drives wall-time too. |
| 1 → 2 | "Which 1–3 of these do you want to lock in?" | Identity is fixed once you commit; later stages can't change the face |
| 2 → 3 | "Does the upscaled front + back still look like the same person?" | Drift here means stage 3 produces views of someone else — and a back that doesn't match the front means the LoRA learns two people. |
| 3 → 4 | "Which seed per pose do you want at full quality?" | Stage 4 burns ~3× the compute — only upscale keepers |

These are user calls, not your calls. Don't auto-pick.

## Estimated wall time on MPS (Apple Silicon)

| Stage | Volume | Per image | Total |
|-------|--------|-----------|-------|
| 1 | 15 × 0.39 MP, 8 steps | ~12s | ~3 min |
| 2 | 1–3 × 1.78 MP, 32 steps × 2 (front + back) | ~110s | ~5–10 min |
| 3 | N poses × 5 seeds × 0.5 MP, 8 steps | ~75s | **~6 min × N** |
| 4 | (2–4 keepers) × 1.5 MP, 12 steps | ~3 min | ~6–12 min |
| **Total** | — | — | **~25 min (turnaround) / ~45 min (character-sheet)** |

Stage 3 scales linearly with pose-pack size — `turnaround.md` (2 poses)
takes ~12 min, `character-sheet.md` (4 poses) takes ~24 min. Tell the
user the estimate that matches their chosen pack when they kick off.

## Tips

- **Run the whole pipeline in one session dir** (`$PIPELINE/`) so checkpoints, picks, and final outputs live together. Easier to revisit later than scattered timestamped dirs.
- **Don't skip the upscale at stage 2.** Going straight from low-res discovery to seed-hunt costs you identity fidelity — the reference image quality propagates to every view.
- **Front + back refs are non-negotiable.** Stage 2 always produces both. Stage 3 uses one per pose based on the angle (front ref for front/side poses, back ref for rear poses). If you only have a front pick, generate the back-view variant before promoting.
- **The seed is the person.** Across stages 1, 2, and inside any single seed-hunt prompt at stage 3, `noise_seed` is what locks the likeness. Never change it accidentally.
- **Stages 1+2 use persephone, stages 3+4 use flux2-klein.** Different model, different node IDs in the workflow JSON — the model-upscale flow handles both via its dispatch table, so trust it.
- **Resumability:** if the user pauses between stages, the session dir tells you where you left off — `1-discover/picks/` exists ⇒ stage 1 done, stage 2 not started; both `front_*.png` and `back_*.png` in `2-reference/` ⇒ ready to promote and start stage 3; etc.
- **Failure recovery:** if any sub-flow batch loses jobs to a ComfyUI restart, that flow's monitoring section tells you how to detect and resubmit. This skill doesn't need to know about queue mechanics.
- **Custom pose packs:** copy `skills/comfyui/character-pipeline/references/poses/turnaround.md`, edit the entries (each pose is a simple natural-language instruction describing the angle and body position), bump the `poses` count in the frontmatter, and either drop the file alongside the bundled packs or hand Stage 0 an absolute path to it. **Don't add front or back entries** — those are covered by the Stage 2 references. See `skills/comfyui/character-pipeline/references/poses/README.md`.
- **Prompts are derived, not composed.** Stage 3 doesn't author fresh prompts from the pose pack. Instead it takes the Stage 2 reference prompt and swaps only the view/positioning clause per pose. The identity blurb, wardrobe, lighting, and lens stay verbatim across all poses. The user approves the derived prompts before the sweep.
