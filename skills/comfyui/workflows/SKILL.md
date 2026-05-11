---
name: comfyui-workflows
description: "Sub-flow of the `comfyui` skill. Read this file with the `read` tool when the user asks to create, edit, debug, or adapt a ComfyUI workflow / graph / pipeline JSON. Triggers from inside the `comfyui` skill: 'comfyui workflow', 'build a comfy graph', 'txt2img / img2img / controlnet pipeline', 'modify this comfyui json', 'add a node to my workflow', 'why is my comfyui workflow failing'. NOT auto-loaded — the top-level `comfyui` skill points at this file."
disabled: true
---

# ComfyUI Workflows

Sub-flow of the `comfyui` skill (this file lives at `skills/comfyui/workflows/SKILL.md`).

Author and edit ComfyUI workflow JSON. Default to the **API `/prompt` format** (a flat object keyed by node ID) — it is the execution format ComfyUI actually consumes. Editor-saved workflow JSON (`workflow.json`) carries extra UI/layout state and should only be touched when the user explicitly hands it over.

## When to load this skill

Load when the user wants to:

- generate a workflow from a natural-language description ("make me an img2img graph that uses ControlNet")
- modify an existing workflow (change a sampler, swap a model, add a LoRA, insert an upscale tail)
- debug a workflow that ComfyUI rejected with a schema error
- adapt a starter graph to a custom node pack the user has installed

If the request is about installing ComfyUI, training models, or general Stable Diffusion theory, this skill is **not** the right one — answer directly.

## Core invariants of `/prompt` JSON

These are non-negotiable. Violate any one and ComfyUI will reject the prompt.

1. **Top-level shape is a flat object** keyed by node ID strings: `{"3": {...}, "4": {...}}`. Node IDs are stringified integers.
2. **Each node has exactly two required keys:** `class_type` (string) and `inputs` (object).
3. **Links are 2-tuples**: `[source_node_id, output_slot_index]`. The source ID must be a string that exists as a top-level key. `output_slot_index` is a 0-based int into the source node's `output` list.
4. **Widget values are scalars** (string / int / float / bool) — never wrapped in a list. Only inter-node connections use the `[id, slot]` form.
5. **Every link target must resolve.** Dangling references = "Prompt outputs failed validation".
6. **Required inputs from `/object_info` must all be present.** Missing a required input is the most common rejection.

## Workflow

1. **Decide format.** If the user pasted JSON, detect which format it is:
   - API `/prompt`: flat dict, each value has `class_type` + `inputs`. Author here.
   - Editor save: has top-level `nodes` array, `links` array, `groups`, `config`. Convert mentally to `/prompt` for reasoning, but preserve the original structure on edits.
   - See `references/formats.md` for the full comparison and conversion notes.

2. **Pick a starting point.** For new workflows, copy a template from `templates/` and adapt:
   - `txt2img.json` — base text-to-image (CheckpointLoader → CLIPTextEncode → KSampler → VAEDecode → SaveImage)
   - `img2img.json` — adds LoadImage + VAEEncode and lower denoise on KSampler
   - `upscale.json` — image upscale via UpscaleModelLoader + ImageUpscaleWithModel
   - `controlnet.json` — ControlNet conditioning via ControlNetLoader + ControlNetApplyAdvanced

3. **Fill node schemas from references first.** Look up node input/output shapes in `references/core_nodes.md`. That covers the common built-ins without touching the live instance.

4. **For unknown / custom / version-specific nodes**, run live introspection (see "Live introspection" below) and read the normalized output rather than guessing.

5. **Allocate node IDs deterministically.** When generating from scratch, number sequentially starting at `"3"` to mirror the conventions you see in real exports (1 and 2 are commonly historical loader IDs in older saves — leaving small gaps is fine). When inserting into an existing graph, use `max(existing_ids) + 1`. Never reuse an existing ID.

6. **Validate before returning to the user.** Check the invariants above by hand, plus:
   - every link source ID exists
   - every required input from the schema is set
   - widget values are scalars, not lists
   - sampler inputs `model` / `positive` / `negative` / `latent_image` all point at compatible types

7. **Submit if asked.** A workflow can be submitted via `POST http://127.0.0.1:8188/prompt` with body `{"prompt": <workflow>}`. Use `bash` + `curl` for one-shot submission; capture the response for the user.

## Editing existing workflows

When modifying a workflow the user has provided:

- **Preserve unknown nodes and fields verbatim.** Do not strip `class_type`s you don't recognize, and do not drop input keys you don't understand. Custom node packs frequently add metadata fields the agent has no schema for.
- **Renumber only when adding nodes.** Existing IDs should stay stable so the user's mental map of the graph survives the edit. New nodes get fresh IDs above the current max.
- **Preserve link integrity after a delete.** If you remove node X, every link that pointed `[X, n]` must be repointed or its consumer node also removed/edited. Leaving a dangling `[X, n]` will fail validation.
- **Match widget order for nodes whose API form is positional.** A few editor-saved nodes encode widget values positionally rather than by name. The `/prompt` format always uses named inputs, but if you are converting to the editor format, the order in `widgets_values` must match the node's declared widget order from `/object_info`.

## Live introspection

ComfyUI exposes node schemas at `GET /object_info`. The skill ships a helper that hits this and writes a normalized, agent-friendly form to disk:

```bash
# default URL is http://127.0.0.1:8188 (override with COMFYUI_URL or --url)
python skills/comfyui/workflows/scripts/fetch_object_info.py --out /tmp/object_info.json

# only fetch a few classes
python skills/comfyui/workflows/scripts/fetch_object_info.py \
    --out /tmp/object_info.json \
    --classes KSampler CheckpointLoaderSimple CLIPTextEncode
```

The normalized output is `{class_name: {display_name, category, description, inputs: {name: {type, required, default?, choices?, min?, max?}}, outputs, output_names}}`. Read it with the standard `read` / `grep` tools.

When to reach for it:

- the user mentions a custom node pack (Impact, Inspire, AnimateDiff, IPAdapter, etc.)
- bundled `references/core_nodes.md` doesn't list the node you need
- a workflow rejection mentions an input that isn't in your reference (likely a version drift)

When to skip it:

- the user is offline / ComfyUI isn't running. Say so and rely on the bundled reference, flagging that custom nodes can't be verified.

## Submitting a workflow

```bash
curl -s -X POST http://127.0.0.1:8188/prompt \
    -H "Content-Type: application/json" \
    -d "$(jq -n --slurpfile p workflow.json '{prompt: $p[0]}')"
```

Successful response is `{"prompt_id": "...", "number": N, "node_errors": {}}`. A non-empty `node_errors` is the structured failure mode — read it back to the user; each entry maps a node ID to the rejected input.

## Common mistakes

- **Authoring in editor-save format by accident.** If the agent is generating from scratch, output `/prompt` JSON. Editor JSON has `nodes: [...]`, `links: [...]`; if your output looks like that, you went the wrong way.
- **Wrapping widget values in lists.** Only links are 2-tuples. `"steps": [20]` will fail; `"steps": 20` is correct.
- **Assuming output slot 0.** Many loaders produce multiple outputs at fixed slots, e.g. `CheckpointLoaderSimple` returns `[MODEL, CLIP, VAE]` at slots 0/1/2. Sampler `model` ← `[loader, 0]`, but `CLIPTextEncode.clip` ← `[loader, 1]`.
- **Stripping unknown fields on edit.** If you don't know what a key does, leave it. Custom nodes ship custom payloads.
- **Renumbering nodes wholesale.** Stable IDs survive edits; renumbering breaks the user's mental model and makes diffs unreadable.
- **Trusting bundled references for custom nodes.** They cover built-ins. Run live introspection for anything else.
- **Not validating before declaring done.** Walk every link; confirm every required input is present.
