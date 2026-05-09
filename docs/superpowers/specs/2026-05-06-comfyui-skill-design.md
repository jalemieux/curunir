# ComfyUI Skill — Design

**Date:** 2026-05-06
**Status:** Design approved, implementation in flight
**Issue:** #76

## Problem

Curunir should drive a local ComfyUI instance end-to-end across turns:
discover available workflows, mutate inputs (prompt, seed, image refs),
submit, poll, fetch generated outputs, and surface them to the user.
Today there's no skill or driver — only an empty `skills/comfyui-workflows/`
placeholder left over from an earlier authoring-only effort.

## Goals

- Let the agent run image and (eventually) video workflows against a
  ComfyUI process running on the same host as the harness.
- Keep the surface small: ComfyUI primitives as a Python CLI, with the
  agent driving the workflow JSON via the existing `edit` tool.
- Ship seed templates for the common cases the user has called out:
  Flux Redux, Flux Kontext, multi-image-in.

## Non-Goals

- A workflow builder. The agent edits JSON; it does not synthesize graphs
  from scratch.
- Remote / cross-host ComfyUI. The driver assumes loopback. If that ever
  needs to change, the only knob is `COMFYUI_URL` plus auth concerns
  outside this design.
- A new runtime tool under `src/tools/`. The `bash` tool plus `comfy.py`
  covers everything we need.

## Architecture

Pure skill + driver. No changes to `src/`.

```
skills/comfyui/
├── SKILL.md              # agent-readable instructions
├── comfy.py              # Python CLI driver (subcommands)
└── templates/
    ├── README.md
    ├── flux-redux.json
    ├── flux-kontext.json
    └── multi-image-in.json
```

The skill is a **driver**, not a workflow library. `comfy.py` exposes
ComfyUI primitives one subcommand at a time; the agent decides what
workflow to run, copies a template into a session directory, edits the
JSON, then submits + waits + fetches via `comfy.py`.

### Why agent edits JSON, not the driver

Workflow mutation is intent-shaped (change the prompt, swap an input
image, bump the seed) and the field names live in the workflow itself.
Building a typed mutation API on top of every node type would either be
incomplete or balloon to mirror ComfyUI's whole node catalog. The agent
already knows how to read JSON and use `edit`; that's the right level.

`_meta.editable` in each template lists the fields the agent should
expect to touch (`KSampler.seed`, `CLIPTextEncode.text`, etc.) so it
doesn't have to grep the whole graph.

## ComfyUI API Surface

`comfy.py` calls four endpoints plus the websocket:

| Endpoint | Method | Used by |
|---|---|---|
| `/object_info` | GET | `models`, `nodes`, submit pre-flight |
| `/prompt` | POST | `submit`, `run` |
| `/history/{id}` | GET | `wait` (polling fallback), `fetch` |
| `/view` | GET | `fetch` (download outputs) |
| `/queue` | GET / POST | `queue`, `cancel` |
| `/interrupt` | POST | `cancel` (running prompt) |
| `ws://.../ws?clientId=…` | WS | `wait` (primary path) |

The websocket emits `executing` frames per node and a final
`{"node": null}` frame when the prompt is done. Loopback WS is reliable;
the polling fallback is a thin safety net, not the primary path.

## CLI Subcommands

All commands speak JSON to stdout. Errors are
`{"error": "...", "hint": "..."}` with exit code 1; usage errors exit 2;
success exits 0.

| Command | Purpose |
|---|---|
| `models` | Filtered checkpoint/LoRA/VAE/ControlNet enums from `/object_info`. |
| `nodes` | All node class names. With `--required <list>`, reports missing classes. |
| `submit <wf.json>` | Pre-flight check then POST `/prompt`. Returns `prompt_id`. |
| `wait <id>` | WS-watch a prompt to completion. Falls back to polling on WS error. |
| `fetch <id> --out <dir>` | Download outputs from `/history/{id}` via `/view`. |
| `run <wf.json> --out <dir>` | submit + wait + fetch in one call. |
| `queue` | `/queue` rollup (running + pending). |
| `cancel <id>` | Delete pending or `/interrupt` running. |
| `history [--limit N]` | Recent prompt IDs. |

### Pre-flight on submit

Before POSTing, `submit` calls `/object_info` and:

1. Confirms every `class_type` referenced in the workflow is available.
2. For known model-loading classes (`CheckpointLoaderSimple`,
   `UNETLoader`, `VAELoader`, `LoraLoader`), confirms the named
   model file is in the live enum.

If a node or model is missing, exits 1 with an actionable hint:

```
Flux Redux model `flux1-redux-dev.safetensors` not found locally —
install via ComfyUI Manager and retry.
```

If the loopback connection is refused, the hint says
"local ComfyUI process isn't running on $COMFYUI_URL".

## Session Layout

```
context/uploads/comfyui/sessions/<ts>/
├── workflow.json    # mutated copy of a template
├── inputs/          # any image inputs pre-uploaded to ComfyUI
└── outputs/         # files downloaded by `comfy.py fetch`
```

The agent surfaces outputs via the `attach` opt-in tool declared in the
skill frontmatter. For video, attach the first frame as a preview plus
the `.mp4` path.

## Configuration

Two env vars, both optional:

- `COMFYUI_URL` — defaults to `http://127.0.0.1:8188`.
- `COMFYUI_DEFAULT_TIMEOUT_S` — defaults to 300.

No auth: same-host loopback, no token surface.

## Testing

`tests/test_comfy_driver.py` mocks ComfyUI with a stdlib HTTP+WS server
on a thread. Coverage:

- `models` parses `/object_info` enums correctly.
- `submit` pre-flight rejects missing nodes.
- `wait` returns timeout cleanly when WS never closes.
- `fetch` writes files with correct names from `/view`.
- `run` chains submit/wait/fetch.
- WS path handles the `executing` end frame; polling fallback kicks in
  when the WS errors.

Manual end-to-end (PR test plan, deferred to reviewer) against a real
ComfyUI: each of the three templates, a missing-model pre-flight, and a
cancel.

## Risks & Open Questions

- **Stdlib vs. `httpx` / `websockets`**: both are already in
  `requirements.txt`, so the driver uses them. Keeps the WS path
  clean.
- **`/object_info` payload size**: large on a loaded ComfyUI but cheap
  over loopback. Cached per `comfy.py` invocation; the skill instructs
  the agent to call `models`/`nodes` once per session, not per submit.
- **Template drift**: ComfyUI nodes evolve. Each template's `_meta`
  records the ComfyUI commit/date it was authored against. No
  auto-migration in v1.
- **Same-host assumption**: hardcoded loopback default. The only path
  to remote use is overriding `COMFYUI_URL`; auth and TLS are out of
  scope here.
- **Workflow inspection**: agent must know which fields to edit. If
  `_meta.editable` proves too thin, follow-up adds
  `comfy.py inspect <wf>` to print the editable surface.
