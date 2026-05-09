# ComfyUI Workflow Formats

ComfyUI has **two** JSON serializations of a workflow. They are not
interchangeable, and a graph round-tripped through the wrong importer often
silently breaks. Know which one you are looking at before editing.

## Format 1: API `/prompt` JSON (preferred for authoring)

This is what ComfyUI's `/prompt` HTTP endpoint consumes, and what is saved
when you use **"Save (API Format)"** in the editor.

**Shape:** flat object keyed by node ID strings.

```json
{
  "3": {
    "class_type": "KSampler",
    "inputs": {
      "seed": 12345,
      "steps": 20,
      "cfg": 8.0,
      "sampler_name": "euler",
      "scheduler": "normal",
      "denoise": 1.0,
      "model": ["4", 0],
      "positive": ["6", 0],
      "negative": ["7", 0],
      "latent_image": ["5", 0]
    }
  },
  "4": { "class_type": "CheckpointLoaderSimple", "inputs": { ... } }
}
```

**Properties of the format:**

- Each node has exactly two top-level keys: `class_type` and `inputs`.
- Widget values (numbers, strings, enums) are scalars in `inputs`.
- Inter-node connections are 2-element arrays: `[source_node_id, output_slot_index]`.
- Node IDs are stringified ints. They are stable but otherwise meaningless;
  ComfyUI does not require contiguous numbering.
- There is **no UI/layout state**: no positions, no link IDs, no group
  metadata.

**Why prefer this for authoring:**

- Smaller and easier to synthesize deterministically.
- 1:1 mapping from intent → JSON: each node, each input.
- No risk of producing layout state that conflicts with the graph data.
- It is the format ComfyUI actually executes.

**Submit:**

```bash
curl -s -X POST http://127.0.0.1:8188/prompt \
    -H "Content-Type: application/json" \
    -d "$(jq -n --slurpfile p workflow.json '{prompt: $p[0]}')"
```

The reply is JSON: `{"prompt_id": "...", "number": N, "node_errors": {...}}`.
A non-empty `node_errors` object is the structured failure mode.

## Format 2: Editor-saved workflow JSON

This is what the **"Save"** button writes. It is the editor's full state,
not just the graph: positions, link IDs, group boxes, viewport, version
metadata.

**Shape (abbreviated):**

```json
{
  "last_node_id": 9,
  "last_link_id": 11,
  "nodes": [
    {
      "id": 3,
      "type": "KSampler",
      "pos": [863, 186],
      "size": [315, 262],
      "flags": {},
      "order": 4,
      "mode": 0,
      "inputs": [
        {"name": "model", "type": "MODEL", "link": 1},
        {"name": "positive", "type": "CONDITIONING", "link": 4},
        ...
      ],
      "outputs": [
        {"name": "LATENT", "type": "LATENT", "links": [7], "slot_index": 0}
      ],
      "properties": {"Node name for S&R": "KSampler"},
      "widgets_values": [12345, "fixed", 20, 8.0, "euler", "normal", 1.0]
    },
    ...
  ],
  "links": [
    [1, 4, 0, 3, 0, "MODEL"],
    [2, 4, 1, 6, 0, "CLIP"],
    ...
  ],
  "groups": [],
  "config": {},
  "extra": {},
  "version": 0.4
}
```

**Properties:**

- `nodes` is an **array**, not a map. Each node has its own integer `id`.
- Connections live in a separate `links` array, where each entry is
  `[link_id, source_node_id, source_slot, target_node_id, target_slot, type]`.
  Each node's `inputs[].link` and `outputs[].links` reference these IDs.
- Widget values are a **positional array** (`widgets_values`), not a named
  map. Order must match the node's declared widget order in `/object_info`.
- Carries layout, mode, group, and viewport state that ComfyUI uses for the
  editor UI.

**When you'll touch this format:**

- The user pasted the output of "Save" instead of "Save (API Format)".
- The user wants edits preserved in the editor (positions, groups).
- You're round-tripping into the visual editor and need the layout to
  survive.

**Editing rules:**

- Preserve `extra`, `version`, `config`, `groups`, and any node fields you
  don't recognize. They are editor state and stripping them produces a
  graph that imports differently.
- When you edit a `widgets_values` array, the order must still match the
  target node's widget ordering. If you don't have the schema, fetch it
  via `scripts/fetch_object_info.py` rather than guessing.
- When you add a node, mint a new node `id` (= `last_node_id + 1`) and
  bump `last_node_id`. Same for links: new link ID = `last_link_id + 1`.

## Conversion notes

### Editor save → API `/prompt`

Walk the `nodes` array. For each node:

1. New entry in the output dict, key = `str(node.id)`.
2. `class_type` = `node.type`.
3. `inputs` starts empty.
4. For each `node.inputs[i]`, look up the link by `node.inputs[i].link`
   in the top-level `links` array. The link entry tells you the source
   node ID and slot. Set `inputs[node.inputs[i].name] = [str(source_id), source_slot]`.
5. Map `widgets_values` to named inputs by reading the node's widget order
   from `/object_info`. The `i`-th widget value goes to the `i`-th widget
   name from the schema. (This is the step that requires live schema
   access if you don't already know the node.)
6. Drop everything else (`pos`, `size`, `mode`, `properties`, ...).

### API `/prompt` → editor save

This is **lossy** — you don't have node positions, link IDs, or group
state. ComfyUI's editor will import API JSON and lay it out automatically;
prefer that path over hand-synthesizing editor JSON.

## Recommendation

**Generate in API `/prompt` format. Edit in whichever format the user
provided.** When in doubt, ask the user to "Save (API Format)" before
sending the workflow over.
