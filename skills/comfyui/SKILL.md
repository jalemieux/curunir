---
name: comfyui
description: "Use when the user asks to generate or edit images (or video) via the local ComfyUI instance — Flux Redux variations, Flux Kontext text-to-image, multi-image composites, or any custom workflow. Trigger: requests like 'generate an image', 'run this through ComfyUI', 'make a variation of this photo', or anything pointing at a local ComfyUI graph."
tools: attach
---

# ComfyUI

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
cp skills/comfyui/templates/flux-redux.json "$SESSION/workflow.json"
```

| Template | Purpose | Read |
|---|---|---|
| `flux-redux.json` | Image variation from a single input image + optional prompt nudge. | `templates/README.md` |
| `flux-kontext.json` | Text-to-image with Flux. Edit `CLIPTextEncode.text` and `KSampler.seed`. | `templates/README.md` |
| `multi-image-in.json` | Two image inputs feeding a composite (style + subject). | `templates/README.md` |
| `flux2-klein-seed-hunt.json` | Bulk seed hunting with Flux 2 Klein + dual image reference. Used by `comfyui-seed-hunt` skill. | `templates/README.md` |
| `persephone-flux-model-seed.json` | Model seed discovery — text-to-image front-view character sheets with random seeds. Used by `comfyui-model-seed-hunt` skill. | `templates/README.md` |

## 3. Edit the workflow JSON

The agent edits the session-copy JSON with the `edit` tool. Common
fields (look these up in the template's `_meta.editable` list):

| Field | Meaning |
|---|---|
| `KSampler.inputs.seed` | Set to a fixed integer for reproducibility, or leave at 0 to randomize. |
| `CLIPTextEncode.inputs.text` | Positive prompt. There is usually a second `CLIPTextEncode` node for the negative. |
| `LoadImage.inputs.image` | Filename of an image already uploaded to ComfyUI's `input/` directory. |
| `EmptyLatentImage.inputs.width` / `height` | Output dimensions. |
| `KSampler.inputs.steps` / `cfg` | Quality knobs. Bump steps for higher quality, lower cfg for less prompt adherence. |

**Image inputs:** ComfyUI reads from its own `input/` directory.
If the user uploaded an image, copy it into ComfyUI's `input/` first
(or use `/upload/image` POST if exposed) and reference it by filename.
Do not put absolute paths in `LoadImage.image`.

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

## 5. Surface the outputs

Use the `attach` opt-in tool (declared in this skill's frontmatter) to
return the file to the user.

- **Image:** attach the `.png` directly.
- **Video / animation:** attach the `.mp4` (or first-frame preview if
  the channel doesn't render video).

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
