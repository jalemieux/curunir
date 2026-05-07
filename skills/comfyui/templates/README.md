# ComfyUI Templates

Seed workflows for the `comfyui` skill. Copy into the session
directory before editing — never edit these in place.

| Template | Purpose | Required models |
|---|---|---|
| `flux-kontext.json` | Text-to-image with Flux. | `flux1-dev.safetensors`, `ae.safetensors`, T5 + CLIP-L |
| `flux-redux.json` | Image variation: one reference image → Flux Redux. | Above + `flux1-redux-dev.safetensors`, sigclip vision |
| `multi-image-in.json` | Two reference images composited. | Same as `flux-redux` |

Each template's `_meta.editable` block lists the fields the agent
should expect to touch (prompt, seed, image filenames, dimensions).
The `_meta` key is stripped before submission to ComfyUI.

## What to edit

| Most-edited | Field |
|---|---|
| Prompt | `6.inputs.text` (the positive `CLIPTextEncode`) |
| Seed | `25.inputs.noise_seed` (`RandomNoise`) — `0` = randomize |
| Output size | `5.inputs.width`, `5.inputs.height` |
| Steps | `17.inputs.steps` |
| Image input | `30.inputs.image` (and `40.inputs.image` for multi) |

## What to leave alone

- Node IDs (`"5"`, `"6"`, etc.) — the wires reference them.
- `class_type` — changing this will hit pre-flight before submit.
- The structural Flux load chain (`UNETLoader` → `BasicGuider` →
  `SamplerCustomAdvanced`).

If a template breaks against a newer ComfyUI version, the `_meta`
block records the version it was authored against. No automatic
migration in v1.
