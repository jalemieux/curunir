# Core ComfyUI Nodes — Authoring Reference

Compact reference for the most-used built-in nodes. Use this for the common
case; for custom-node packs or to verify against a specific ComfyUI version,
run `scripts/fetch_object_info.py` and read the normalized JSON instead.

All input/output type names below match what `/object_info` returns
(`MODEL`, `CLIP`, `VAE`, `LATENT`, `IMAGE`, `CONDITIONING`, `INT`,
`FLOAT`, `STRING`, etc.).

## Loaders

### CheckpointLoaderSimple
- **inputs:** `ckpt_name` (STRING / file enum)
- **outputs:** `[MODEL, CLIP, VAE]` — slots `0`, `1`, `2`
- Most pipelines wire `model` to a sampler from slot 0, `clip` to a text
  encoder from slot 1, and `vae` to VAEDecode/Encode from slot 2.

### LoraLoader
- **inputs:** `model` (MODEL), `clip` (CLIP), `lora_name` (STRING / file
  enum), `strength_model` (FLOAT), `strength_clip` (FLOAT)
- **outputs:** `[MODEL, CLIP]`
- Chain by feeding the previous loader's MODEL/CLIP into this one's inputs;
  downstream nodes should consume from this LoraLoader, not the original
  checkpoint.

### VAELoader
- **inputs:** `vae_name` (STRING / file enum)
- **outputs:** `[VAE]`
- Use when you want a VAE separate from the one bundled in a checkpoint.

### CLIPLoader / DualCLIPLoader
- **inputs:** `clip_name` (STRING) [+ `clip_name2`, `type` for Dual]
- **outputs:** `[CLIP]`

### ControlNetLoader
- **inputs:** `control_net_name` (STRING / file enum)
- **outputs:** `[CONTROL_NET]`

### UpscaleModelLoader
- **inputs:** `model_name` (STRING / file enum)
- **outputs:** `[UPSCALE_MODEL]`

### LoadImage
- **inputs:** `image` (filename string), `upload` (`"image"`)
- **outputs:** `[IMAGE, MASK]` — slot 0 is the image, slot 1 is the mask if
  the file has alpha.

## Encoders / Conditioners

### CLIPTextEncode
- **inputs:** `text` (STRING, multiline), `clip` (CLIP)
- **outputs:** `[CONDITIONING]`
- Build a positive prompt and a negative prompt with two separate
  CLIPTextEncode nodes; both feed into the sampler.

### VAEEncode
- **inputs:** `pixels` (IMAGE), `vae` (VAE)
- **outputs:** `[LATENT]`

### VAEEncodeForInpaint
- **inputs:** `pixels` (IMAGE), `vae` (VAE), `mask` (MASK), `grow_mask_by`
  (INT)
- **outputs:** `[LATENT]`

### VAEDecode
- **inputs:** `samples` (LATENT), `vae` (VAE)
- **outputs:** `[IMAGE]`

### EmptyLatentImage
- **inputs:** `width` (INT), `height` (INT), `batch_size` (INT)
- **outputs:** `[LATENT]`
- Default for txt2img. Use VAEEncode for img2img instead.

## Samplers

### KSampler
- **inputs:** `model` (MODEL), `seed` (INT), `steps` (INT), `cfg` (FLOAT),
  `sampler_name` (ENUM), `scheduler` (ENUM), `positive` (CONDITIONING),
  `negative` (CONDITIONING), `latent_image` (LATENT), `denoise` (FLOAT)
- **outputs:** `[LATENT]`
- For txt2img leave `denoise=1.0`. For img2img drop to `0.5–0.85`.
- `sampler_name` enum commonly includes: `euler`, `euler_ancestral`,
  `dpmpp_2m`, `dpmpp_sde`, `ddim`, `uni_pc`. Verify against `/object_info`
  for your build.
- `scheduler` enum: `normal`, `karras`, `exponential`, `simple`, `ddim_uniform`.

### KSamplerAdvanced
- **inputs:** `model`, `add_noise` (`enable`/`disable`), `noise_seed`,
  `steps`, `cfg`, `sampler_name`, `scheduler`, `positive`, `negative`,
  `latent_image`, `start_at_step` (INT), `end_at_step` (INT),
  `return_with_leftover_noise`
- **outputs:** `[LATENT]`
- Use when chaining samplers (e.g. base + refiner). `start_at_step` /
  `end_at_step` slice the sampling schedule.

## ControlNet

### ControlNetApply (legacy)
- **inputs:** `conditioning` (CONDITIONING), `control_net` (CONTROL_NET),
  `image` (IMAGE), `strength` (FLOAT)
- **outputs:** `[CONDITIONING]`
- Older form; only modifies one conditioning branch.

### ControlNetApplyAdvanced
- **inputs:** `positive` (CONDITIONING), `negative` (CONDITIONING),
  `control_net` (CONTROL_NET), `image` (IMAGE), `strength` (FLOAT),
  `start_percent` (FLOAT), `end_percent` (FLOAT)
- **outputs:** `[CONDITIONING, CONDITIONING]` — slot 0 is the modified
  positive, slot 1 the modified negative; feed both straight into the
  sampler.
- Prefer this over the legacy form for new graphs.

## Image Operations

### ImageUpscaleWithModel
- **inputs:** `upscale_model` (UPSCALE_MODEL), `image` (IMAGE)
- **outputs:** `[IMAGE]`

### ImageScale
- **inputs:** `image` (IMAGE), `upscale_method` (ENUM: nearest-exact,
  bilinear, bicubic, area, lanczos), `width` (INT), `height` (INT),
  `crop` (ENUM: disabled, center)
- **outputs:** `[IMAGE]`

### LatentUpscale
- **inputs:** `samples` (LATENT), `upscale_method` (ENUM), `width` (INT),
  `height` (INT), `crop` (ENUM)
- **outputs:** `[LATENT]`

### LatentUpscaleBy
- **inputs:** `samples` (LATENT), `upscale_method` (ENUM), `scale_by` (FLOAT)
- **outputs:** `[LATENT]`

## Sinks

### SaveImage
- **inputs:** `images` (IMAGE), `filename_prefix` (STRING)
- **outputs:** none
- Writes to ComfyUI's `output/` directory.

### PreviewImage
- **inputs:** `images` (IMAGE)
- **outputs:** none
- Use when you don't want to persist the result.

## Slot index cheatsheet

- `CheckpointLoaderSimple` → MODEL=0, CLIP=1, VAE=2
- `LoraLoader` → MODEL=0, CLIP=1
- `LoadImage` → IMAGE=0, MASK=1
- `ControlNetApplyAdvanced` → POS=0, NEG=1
- Most everything else has a single output at slot 0.

## What this file does *not* cover

- Custom node packs (Impact, IPAdapter, AnimateDiff, ComfyUI-Manager…). Use
  `scripts/fetch_object_info.py` to fetch their schemas.
- Niche built-ins (segmentation, audio, 3D nodes). Same — go live.
- ComfyUI version drift: input names occasionally change between releases.
  When in doubt, the live `/object_info` is authoritative.
