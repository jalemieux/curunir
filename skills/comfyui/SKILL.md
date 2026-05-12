---
name: comfyui
description: "Load this for ANY local image/video generation work — even if the user never says the word 'comfyui'. Covers Flux (Flux Redux, Flux Kontext, Flux1, Flux2 Klein, Persephone Flux), PuLID, InstantX, IP-Adapter, ControlNet, LoRA references, seed hunts, upscales, multi-view character sheets, txt2img / img2img / inpaint / outpaint / variations, custom node packs, and the workflow JSON itself. Sub-skills under `skills/comfyui/` handle deeper flows (character pipeline, workflow JSON authoring). Triggers (any of these): 'comfyui', 'comfy', 'flux', 'redux', 'kontext', 'klein', 'persephone', 'pulid', 'ip-adapter', 'instantx', 'controlnet', 'lora', 'txt2img', 'img2img', 'inpaint', 'outpaint', 'seed hunt', 'upscale', 'character sheet', 'multi-view', 'generate an image', 'make a variation', 'run this through the graph', 'edit this workflow json', 'why is my workflow failing', or anything pointing at a local image-generation graph / node pipeline."
tools: attach
---

# ComfyUI

**Top-level entry point for everything ComfyUI.** If a user mentions ComfyUI, comfy, image/video generation via a local graph, character sheets, workflow JSON, or anything in that orbit — start here. The basics are in this file; deeper specialized flows are sub-skills under this directory that you read on demand with the `read` tool.

## Sub-skills (read on demand)

| When the user wants… | Read this |
|---|---|
| To author, edit, debug, or adapt a ComfyUI workflow JSON (txt2img/img2img/controlnet, custom nodes, schema errors) | `skills/comfyui/workflows/SKILL.md` |
| End-to-end character sheet generation via Kontext: single reference → multi-angle LoRA training set | `skills/comfyui/character-pipeline/SKILL.md` |

These are sub-flows, not standalone skills — they're not in the top-level manifest. Read them as needed; you're already in the right skill.

## Technical research: Gemini augmentation

When the user asks **conceptual or technical questions** about ComfyUI techniques, best practices, or workflow design — and your training data might be stale or incomplete — **augment with Gemini grounded search** before answering.

**Triggers** (any of these):
- "What's the best way to preserve identity in Flux?"
- "How do I make realistic faces?"
- "What's the optimal sampler for character consistency?"
- "How does PuLID compare to InfiniteYou?"
- "Best practices for LoRA training data?"
- "How to avoid overfitting a LoRA?"
- "What's the current state of the art for X?"
- Any question where the answer involves evolving techniques, recent model releases, or community best practices

**How to use:**

1. Load the `gemini-search` skill
2. Formulate a specific query about the technique/question
3. Run the grounded search:
```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=$GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "QUERY ABOUT COMFYUI TECHNIQUE — be specific about model, workflow, or goal"}]}],
    "tools": [{"google_search": {}}]
  }' | jq -r '{text: .candidates[0].content.parts[0].text, sources: [.candidates[0].groundingMetadata.groundingChunks[]?.web | {title, uri}]}'
```
4. Synthesize the Gemini results with your own knowledge
5. Always cite sources when Gemini provided them

**When NOT to use Gemini:**
- The question is about driving the local instance (running workflows, fixing errors, queue management) → use `comfy.py` directly
- You're executing a known workflow or template → just run it
- The answer is in the skill files or templates → read those first
- The user wants you to *do* something (generate, edit, upscale) → execute, don't research

**Good Gemini queries:**
- "Flux Kontext best practices for identity preservation 2026"
- "How to prevent LoRA overfitting with small datasets"
- "PuLID vs InfiniteYou face injection comparison"
- "Optimal sampler settings for Flux 1 dev on Apple Silicon MPS"

**Bad Gemini queries (don't waste the call):**
- "How do I submit a workflow to ComfyUI?" → that's in `comfy.py`
- "What does the timestep_zero_index error mean?" → that's in memory/archives

## The basics: drive the local instance

Drive the local ComfyUI instance: discover what's installed, copy a
template workflow, edit prompts/seeds/inputs, submit, wait, fetch the
outputs, attach them to the response.

**Assumes:** ComfyUI is running on the same host. Default URL is
`http://127.0.0.1:8188`. Override with `COMFYUI_URL` if needed.

The driver lives at `skills/comfyui/comfy.py`. Invoke it via `bash`. All
subcommands print JSON to stdout. Errors are
`{"error": "...", "hint": "..."}` with exit code 1.

## Workflow loop

```
1. Pre-flight   →  comfy.py models / nodes (once per session)
2. Pick template → copy templates/<name>.json into the session dir
3. Edit JSON    →  use the `edit` tool on the session copy
4. Run          →  comfy.py run <wf.json> --out <session/outputs>
5. Surface      →  attach the output files
```

## 1. Pre-flight (run once per session)

Before touching a workflow, confirm the local install has what you
need.

```bash
python skills/comfyui/comfy.py models
# → {"checkpoints": [...], "loras": [...], "vaes": [...], "controlnets": [...]}

python skills/comfyui/comfy.py nodes --required KSampler,FluxGuidance,LoadImage
# → {"available": [...], "missing": []}
```

If `models` exits 1 with a connection-refused hint, the local ComfyUI
isn't running — tell the user to start it. If the template you want to
use names a model that isn't in the `models` output, stop and tell the
user which model is missing instead of submitting and waiting on a
queue failure.

## 2. Copy a template

Templates live in `skills/comfyui/templates/`. Each has a `_meta` block
listing required models and editable fields.

```bash
TS=$(date +%Y%m%d-%H%M%S)
SESSION=context/uploads/comfyui/sessions/$TS
mkdir -p "$SESSION/outputs"
cp skills/comfyui/templates/flux-kontext-edit.json "$SESSION/workflow.json"
```

| Template | Purpose | Read |
|---|---|---|
| `flux-kontext-edit.json` | **Local Flux Kontext in-context editing.** Source image + text instruction → edited output preserving identity. The go-to for pose/expression/clothing/environment changes. | `templates/README.md` |
| `flux-redux.json` | Image variation from a single input image + optional prompt nudge. | `templates/README.md` |
| `multi-image-in.json` | Two image inputs feeding a composite (style + subject). | `templates/README.md` |
| `flux2-klein-seed-hunt.json` | Bulk seed hunting with Flux 2 Klein + dual image reference. | `templates/README.md` |
| `persephone-flux-model-seed.json` | Model seed discovery — text-to-image front-view character sheets with random seeds. | `templates/README.md` |
| `sdxl-txt2img.json` | Standard SDXL 1.0 txt2img — dual-CLIP encode (`text_g`/`text_l`), CLIP-skip −2, KSampler. Use the `sdxl-prompting` skill to write the prompt. | `templates/README.md` |

## 3. Edit the workflow JSON

The agent edits the session-copy JSON with the `edit` tool. Common
fields (look these up in the template's `_meta.editable` list):

| Field | Meaning |
|---|---|
| `41.inputs.image` | Source image filename (Kontext edit template). |
| `6.inputs.text` | Edit instruction prompt (Kontext) or positive prompt (txt2img). |
| `25.inputs.noise_seed` | Set to a fixed integer for reproducibility, or leave at 0 to randomize. |
| `26.inputs.guidance` | Flux guidance scale (Kontext default 2.5). |
| `27.inputs.width` / `height` | Output dimensions. |
| `17.inputs.steps` | Sampler steps. Bump to 30-40 for finer detail. |

**Image inputs:** ComfyUI reads from its own `input/` directory.
If the user uploaded an image, copy it into ComfyUI's `input/` first
and reference it by filename. Do not put absolute paths in `LoadImage.image`.

## 4. Run it

The shortcut chains submit + wait + fetch:

```bash
python skills/comfyui/comfy.py run "$SESSION/workflow.json" --out "$SESSION/outputs"
# → {"status": "done", "prompt_id": "...", "files": [{"path": "...", "node": "9", "kind": "images"}]}
```

For long-running jobs you can split it:

```bash
python skills/comfyui/comfy.py submit "$SESSION/workflow.json"
# → {"prompt_id": "abc", "queue_position": 1, "client_id": "..."}

python skills/comfyui/comfy.py wait abc --client-id <id> --timeout 600
# → {"status": "done", "outputs": {...}}

python skills/comfyui/comfy.py fetch abc --out "$SESSION/outputs"
```

`run` and `wait` use the WebSocket by default and fall back to polling
`/history/{id}` if the WS errors. Pass `--poll-only` to skip the WS.

### Batch runs (character sheets, multi-pose)

For generating multiple variations from the same source, create per-pose workflow files and submit them all, then wait for each:

```bash
# Create workflows (see character-pipeline skill for the full batch pattern)
for pose in front profile_left profile_right ...; do
  # copy template, edit prompt for this pose, save as wf_${pose}.json
done

# Submit all
for wf in wf_*.json; do
  python skills/comfyui/comfy.py submit "$wf"
done

# Wait for all (sequential — ComfyUI processes one at a time)
python skills/comfyui/comfy.py wait <id> --timeout 900
# ... repeat for each ID
```

## 5. Surface the outputs

Use the `attach` opt-in tool (declared in this skill's frontmatter) to
return the file to the user.

- **Image:** attach the `.png` directly.
- **Video / animation:** attach the `.mp4` (or first-frame preview if
  the channel doesn't render video).
- **Character sheet:** stitch individual images into a grid with Pillow, attach the composite.

## Iteration tips

- Keep the session dir for the conversation. Re-edit `workflow.json`
  and re-`run` to iterate on prompt/seed/image without recopying.
- Seed of `0` means "randomize" in most ComfyUI samplers. Set a real
  integer when you want to reproduce a previous result.
- For prompt comparison, change *only* the seed across runs.
- Pre-flight is cheap over loopback but `/object_info` is large — call
  `models` and `nodes` once per session, not before every submit.

## Cancellation & queue inspection

```bash
python skills/comfyui/comfy.py queue
# → {"running": [[1, "id-r"]], "pending": [[2, "id-p"]]}

python skills/comfyui/comfy.py cancel <prompt_id>
# Pending → DELETE from queue. Running → /interrupt.
```

## Common errors and what they mean

| Symptom | Meaning | Fix |
|---|---|---|
| `error: could not reach ComfyUI at http://127.0.0.1:8188` | ComfyUI isn't running on this host. | Tell the user to start ComfyUI; do not retry. |
| `error: workflow references unknown node classes: X` | Custom node `X` isn't installed. | Tell the user to install it via ComfyUI Manager. |
| `error: required model not found locally: ...` | Pre-flight saw a missing checkpoint/LoRA/VAE. | Tell the user the exact filename to install. |
| `wait` returns `{"status": "timeout"}` | Job exceeded the timeout (default 300s). | Re-run `wait <id> --timeout 1800` if the queue is slow. |
| `wait` returns `{"status": "error", "error": "..."}` | ComfyUI's executor raised. | Read the message — it usually points at the offending node by name. |

## Tips

- **JSON in, JSON out.** All subcommands are pipe-friendly. Use `jq`
  to pluck fields: `comfy.py models | jq '.checkpoints'`.
- **`run` is the happy path.** Use the split commands only when you
  want to do something between steps (e.g. show the user a queue
  position, or hand off `wait` to a longer-running session).
- **Don't edit the templates in place.** Always copy into the session
  dir first; the templates are checked-in starting points.
- **`_meta` is yours.** It's stripped before submission, so you can
  put session notes or prompt history there without confusing
  ComfyUI.
