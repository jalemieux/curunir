# Pose packs

Pose packs are the **input vocabulary** for the
`comfyui-multiview-seed-hunt` skill. Each pack is a markdown file that
defines a set of pose entries; when the skill runs, the agent reads
the chosen pack and derives one flux2-quality prompt per pose by
swapping the pose instruction into the Stage 2 reference prompt.

The bundled packs are **starting points**, not a canonical taxonomy.
The right pose set depends on what the character will be used for
downstream (LoRA training, fashion editorial, comic panels, action
shots, NSFW work, etc.). When none of the bundled packs fit, copy one
and edit it — the format is plain markdown.

A pack can be referenced by **bare name** (resolved against this
directory) or by **absolute path** (anywhere on disk). Callers that
need to enumerate the bundled packs read this directory.

## File format

```markdown
---
name: <slug — must match filename without .md>
description: <one line; shown to the user when listing packs>
poses: <integer; count of pose entries below>
---

# <Pack title>

<Optional 1–3 sentence framing of what this pack is for and why
the poses are chosen.>

## Poses

### <pose-label-1>
<simple natural-language instruction describing the pose, camera angle, and subject position>

### <pose-label-2>
<simple natural-language instruction describing the pose, camera angle, and subject position>
```

## Writing pose instructions

Each pose entry is a **single line of plain English** (or a short
paragraph) that describes the pose the character should be in. Think
of it as a quick instruction you'd give a photographer or a model.

**Examples:**

- `wide angle back view of a woman kneeling on all fours`
- `wide angle view of a man facing the camera sitting on a chair`
- `three-quarter left profile, standing with weight on back leg, arms crossed`
- `low-angle hero shot, feet planted shoulder-width apart, fists on hips`
- `side profile facing left, leaning against a wall with one leg bent`
- `rear three-quarter view, seated on the edge of a table, legs dangling`

**Rules:**

- **Be concrete about body position.** Say what the body is doing — kneeling, sitting, leaning, standing with weight shifted, arms folded, etc. Don't just say "dynamic pose" or "powerful stance."
- **Include the camera angle.** Every pose should state the viewing direction: front view, side profile, three-quarter, rear view, low-angle, wide angle, etc.
- **Keep it short and direct.** 1–2 sentences max. The agent will expand this into flux-compliant prompt language when deriving the actual prompt.
- **No quality tags, no stacked adjectives, no negatives.** The agent handles flux compliance — don't pre-optimize the instruction. "woman standing casually, hands in pockets" beats "beautiful confident powerful woman standing majestically."
- **Don't describe identity, wardrobe, lighting, or lens.** Those come from the Stage 2 reference prompt. The pose instruction only covers angle and body position.
- **Keep poses near-symmetrical when targeting klein.** The skill uses flux2-klein (9B). Klein has weaker anatomy than larger flux2 variants — asymmetric / contorted poses fail more often.

## How the skill consumes a pack

For each pose entry, the agent takes the **Stage 2 reference prompt**
and swaps in the pose instruction, making it flux-compliant:

1. Identify the view/positioning clause in the Stage 2 prompt.
2. Replace it with the pose instruction, rephrased to match the
   flux2 prompt style (see `flux2-prompt` skill for rules).
3. Keep everything else verbatim — identity blurb, wardrobe, lighting,
   lens.

The result is a list of `(label, prompt_text)` pairs that becomes the
`PROMPTS` config for the seed-hunt batch.

The caller is expected to **show the user the composed prompts and
get approval before submitting** the sweep.

## Authoring a custom pack

1. Copy a bundled pack (`turnaround.md` is the simplest) into this
   directory or anywhere on disk.
2. Rename it; update `name` in frontmatter to match the new filename.
3. Replace the pose entries with your own simple instructions.
4. Bump `poses` in frontmatter to the new count.
5. Pass the pack to the multi-view skill — either the bare name (if
   you dropped it under `references/poses/`) or the absolute path.

No code changes required.

## Bundled packs

| Pack | Poses | Use for |
|---|---|---|
| `turnaround.md` | 2 | LoRA training — left + right side profiles (front + back come from Stage 2 references) |
| `character-sheet.md` | 4 | Richer LoRA reference set — three-quarters and profiles on both sides |

Front and back are deliberately **not** in either pack. The character
pipeline produces those as hi-res reference images in Stage 2 and
feeds them to the multi-view sweep as reference images. Adding
front/back to a pack just re-rolls the references and wastes compute.

Action, fashion, editorial, and NSFW packs are intentionally not
bundled — those use cases want enough taste that a stock pack would
mislead more often than it'd help. Author them yourself.
