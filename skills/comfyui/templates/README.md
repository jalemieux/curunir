# ComfyUI Templates

Seed workflows for the `comfyui` skill. Copy into the session
directory before editing — never edit these in place.

| Template | Purpose | Required models |
|---|---|---|
| `flux-kontext-edit.json` | **Local Flux Kontext in-context editing.** Source image + text instruction → edited output preserving identity. The go-to for pose changes, expression swaps, clothing changes, environment swaps. Uses `ReferenceLatent` to feed source into conditioning. | `flux1-kontext-dev.safetensors`, `ae.safetensors`, `t5xxl_fp16.safetensors`, `clip_l.safetensors` |
| `flux-kontext.json` | Text-to-image with Flux (legacy, txt2img only — use `flux-kontext-edit.json` for Kontext editing). | `flux1-dev.safetensors`, `ae.safetensors`, T5 + CLIP-L |
| `flux-redux.json` | Image variation: one reference image → Flux Redux. | Above + `flux1-redux-dev.safetensors`, sigclip vision |
| `multi-image-in.json` | Two reference images composited. | Same as `flux-redux` |
| `flux2-klein-seed-hunt.json` | Bulk seed hunting with Flux 2 Klein + dual image reference. | `flux-2-klein-base-9b.safetensors`, `vae/flux2-vae.safetensors`, Klein CLIP, LoRA |
| `persephone-flux-model-seed.json` | Model seed discovery — pure text-to-image front-view character sheets with random seeds. | `persephoneFluxNSFWSFW_20FP16.safetensors`, `flux_vae.safetensors`, CLIP-L + T5 |
| `sdxl-txt2img.json` | Standard SDXL 1.0 txt2img — dual-CLIP encode, CLIP-skip -2, KSampler. Pair with the `sdxl-prompting` skill for prompt construction. | any SDXL 1.0 checkpoint (default `epicrealismXL_pureFix.safetensors`) |

Each template's `_meta.editable` block lists the fields the agent
should expect to touch (prompt, seed, image filenames, dimensions).
The `_meta` key is stripped before submission to ComfyUI.

## What to edit

| Most-edited | Field |
|---|---|
| Source image | `41.inputs.image` (LoadImage — Kontext edit template) |
| Edit instruction | `6.inputs.text` (positive CLIPTextEncode) |
| Seed | `25.inputs.noise_seed` (`RandomNoise`) — `0` = randomize |
| Output size | `27.inputs.width`, `27.inputs.height` (EmptySD3LatentImage) |
| Steps | `17.inputs.steps` |
| Guidance | `26.inputs.guidance` (FluxGuidance — Kontext default 2.5) |

## What to leave alone

- Node IDs (`"5"`, `"6"`, etc.) — the wires reference them.
- `class_type` — changing this will hit pre-flight before submit.
- The structural Flux load chain (`UNETLoader` → `ModelSamplingFlux` → `BasicGuider` → `SamplerCustomAdvanced`).
- The `FluxKontextImageScale` → `VAEEncode` → `ReferenceLatent` chain — this is how the source image feeds into Kontext's conditioning.

If a template breaks against a newer ComfyUI version, the `_meta`
block records the version it was authored against. No automatic
migration in v1.
