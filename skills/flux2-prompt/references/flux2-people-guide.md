# FLUX.2 Prompting Guide: People, Shots & Poses

**Last updated:** May 7, 2026  
**Applies to:** FLUX.2 (pro, max, flex, dev, klein)  
**Supersedes:** FLUX.1 Prompting Guide (January 2025)

---

## Table of Contents

1. [Understanding FLUX.2's Approach to People](#1-understanding-flux2s-approach-to-people)
2. [FLUX.2 Model Variants — Which to Use for People Shots](#2-flux2-model-variants--which-to-use-for-people-shots)
3. [Anatomy of a People Prompt](#3-anatomy-of-a-people-prompt)
4. [Getting Full Body Shots Right](#4-getting-full-body-shots-right)
5. [Camera Shots & Framing Control](#5-camera-shots--framing-control)
6. [Controlling Poses & Body Language](#6-controlling-poses--body-language)
7. [Aspect Ratio & Resolution Strategies](#7-aspect-ratio--resolution-strategies)
8. [Prompt Order & Layout Control](#8-prompt-order--layout-control)
9. [Describing Body Types Realistically](#9-describing-body-types-realistically)
10. [Hands, Limbs & Anatomy Pitfalls](#10-hands-limbs--anatomy-pitfalls)
11. [Multi-Reference Image Support for Character Consistency](#11-multi-reference-image-support-for-character-consistency)
12. [Using ControlNet with FLUX.2](#12-using-controlnet-with-flux2)
13. [FLUX.2 Structured Prompts & JSON Templates](#13-flux2-structured-prompts--json-templates)
14. [Negative Prompts & Common Fixes](#14-negative-prompts--common-fixes)
15. [FLUX.2 Klein: Anatomy Fixes for Local Users](#15-flux2-klein-anatomy-fixes-for-local-users)
16. [Hex Color Prompting for Clothing & Outfit Control](#16-hex-color-prompting-for-clothing--outfit-control)
17. [Copy-Paste Prompt Templates](#17-copy-paste-prompt-templates)
18. [Quick Troubleshooting Cheat Sheet](#18-quick-troubleshooting-cheat-sheet)
19. [Sources](#19-sources)

---

## 1. Understanding FLUX.2's Approach to People

FLUX.2 is not an incremental update — it is a fundamentally different model under the hood, and understanding *why* it differs from FLUX.1 is the key to writing better prompts.

### The Architecture Shift

| Aspect | FLUX.1 | FLUX.2 |
|--------|--------|--------|
| **Text encoder** | CLIP + T5 | Mistral Small 3.1 (24B VLM) |
| **VAE** | SDXL-era VAE | FLUX.2 VAE (retrained from scratch) |
| **Max native resolution** | 1MP (~1024×1024) | 4MP (2048×2048) |
| **Text rendering** | ~0% legible | ~60% legible |
| **Attribute mixing rate** | ~30% | ~15% |
| **Hand/finger error rate** | Baseline | ~30% reduction vs FLUX.1 |
| **Multi-reference images** | Not supported | Up to 10 reference images |
| **Multi-language prompting** | Limited | Natively supported |

### What Mistral Small 3.1 Changes for You

FLUX.2's encoder is a *vision-language model*, not a bag-of-words text encoder. This has profound implications:

1. **Sentence structure matters.** FLUX.2 parses subject–verb–object relationships. "A woman holding a red umbrella in front of a blue car" is understood as a scene with spatial relationships, not five independent concepts.
2. **Context is preserved across clauses.** Multi-clause prompts with conditional logic work far better. "A chef in a white kitchen, plating a dish with tweezers, while steam rises from a pan behind him" keeps all three actions coherent.
3. **World knowledge is baked in.** The model has genuine understanding of physics, spatial logic, anatomy, and material properties. This means more physically plausible poses, correct lighting interactions, and better scene coherence.
4. **Keyword salad is counterproductive.** Comma-separated tags like "8k masterpiece best quality detailed portrait woman beautiful" waste tokens and can actively confuse the model. Natural conversational language consistently outperforms tag lists.

### The Golden Rule for FLUX.2

> **Describe what you want to see, in natural language, with the most important elements first.**

Quality defaults are professional-grade. You never need to ask for quality — you need to ask for *specificity*.

---

## 2. FLUX.2 Model Variants — Which to Use for People Shots

FLUX.2 comes in five variants, each with different trade-offs for people photography.

### Variant Overview

| Variant | Type | Params | Max Resolution | Neg. Prompts | Steps Control | Best For |
|---------|------|--------|---------------|-------------|---------------|----------|
| **pro** | Cloud API | — | 4MP | [No] | [No] | Production-quality portraits, highest fidelity |
| **max** | Cloud API | — | 4MP | [No] | [No] | Maximum quality output, slower generation |
| **flex** | Cloud API | — | 4MP | [No] | [Yes] | Text rendering, fine detail, developer control |
| **dev** | Open weight (non-commercial) | 32B | 4MP* | [Yes] (via ComfyUI) | [Yes] | Local generation, ControlNet, experimentation |
| **klein** | Open weight (Apache 2.0) | 9B / 4B | 1–2MP* | [Yes] (via ComfyUI) | [Yes] | Consumer GPUs, real-time, prototyping |

\* *Actual achievable resolution depends on available VRAM.*

### People-Shot Recommendations by Variant

**For the best possible people shots:** Use **FLUX.2 pro** or **max**. These produce the most anatomically correct, photorealistic results with minimal effort. No negative prompts means you must describe what you want positively — see [Section 14](#14-negative-prompts--common-fixes).

**For developer control and text rendering:** Use **flex**. It exposes steps and guidance scale, excels at fine details (useful for clothing textures, jewelry, text on clothing), and still delivers pro-level quality.

**For local generation with ControlNet:** Use **dev**. It supports negative prompts and all ControlNet adapters, making it the best local option for pose control, composition guidance, and character consistency workflows.

**For consumer hardware / fast iteration:** Use **klein**. It runs on a single consumer GPU with ~13GB VRAM (FP8). However, klein has significantly more anatomy issues — see [Section 15](#15-flux2-klein-anatomy-fixes-for-local-users) for critical tuning advice.

### Hardware Requirements for Local Users

| Variant | Quantization | VRAM @ 1MP | VRAM @ 2MP | VRAM @ 4MP | Notes |
|---------|-------------|------------|------------|------------|-------|
| **dev (32B)** | FP8 | ~18.2 GB | ~28.4 GB | ~48+ GB | Needs 24GB+ GPU for 1MP comfortably |
| **dev (32B)** | FP4/INT4 | ~12 GB | ~22 GB | — | Quality loss noticeable in fine details |
| **klein (9B)** | FP8 | ~9 GB | ~13 GB | — | Sweet spot for RTX 4070/4080 class |
| **klein (4B)** | FP8 | ~6 GB | ~9 GB | — | Runs on RTX 4060 and below |

> **Tip:** For most local users doing people work, **klein 9B at 1MP** with good prompting produces excellent results for iteration. Do your creative exploration there, then send your best prompts to pro/max for final output.

---

## 3. Anatomy of a People Prompt

A well-structured FLUX.2 prompt follows a priority hierarchy. Word order matters more than ever — the Mistral VLM encoder pays stronger attention to what comes first.

### The Priority Order (Official BFL Framework)

```
Main subject → Key action → Critical style → Essential context → Secondary details
```

### Prompt Length Guide

| Length | Word Count | Use Case |
|--------|-----------|----------|
| **Short** | 10–30 words | Exploration, style testing, mood boards |
| **Medium** | 30–80 words | **Ideal for most people shots** |
| **Long** | 80+ words | Complex scenes, multiple people, editorial compositions |

### Deconstructing a People Prompt

Here's a medium-length portrait prompt with each component labeled:

```
A 35-year-old woman with olive skin and shoulder-length dark brown hair,
[MAIN SUBJECT]

standing at a wooden outdoor café table,
[KEY ACTION + LOCATION]

photographed in warm golden-hour light with a shallow depth of field,
[CRITICAL STYLE + LIGHTING]

wearing a cream linen blazer over a white silk camisole.
[ESSENTIAL CONTEXT - CLOTHING]

In the background, blurred Mediterranean architecture and a cypress tree.
[SECONDARY DETAILS]
```

### What Each Component Controls

| Component | What It Does | Example Phrases |
|-----------|-------------|-----------------|
| **Main subject** | Defines who/what | "A tall man in his 40s with a salt-and-pepper beard" |
| **Key action** | Defines what they're doing | "adjusting his wristwatch while leaning against a brick wall" |
| **Critical style** | Defines the look/feel | "editorial fashion photography, shot on 35mm film" |
| **Essential context** | Defines the environment/clothing | "in a dimly lit jazz bar, wearing a navy double-breasted suit" |
| **Secondary details** | Fills in background/props | "a saxophonist performing in the background, cigarette smoke curling upward" |

### What NOT to Include

Remove these from your prompts — they waste tokens and can reduce quality:

- ❌ Quality tags: "8k, masterpiece, best quality, highly detailed, 4k, award-winning"
- ❌ Redundant style prefixes: "photorealistic, realistic photo, hyperrealistic" (already the default)
- ❌ Artist names unless you genuinely want that artist's style: "by greg rutkowski" adds a specific painterly style
- ❌ Negative quality in positive prompts: "no extra fingers, not blurry" — describe what you *want* instead
- ❌ Excessive adjectives: "beautiful stunning gorgeous gorgeous" — one descriptor is enough

---

## 4. Getting Full Body Shots Right

Full body shots remain one of the hardest tasks for image generation. FLUX.2 handles them significantly better than FLUX.1, but you still need to be intentional.

### The Full Body Checklist

1. **Use an appropriate aspect ratio.** Portrait ratios (9:16, 3:4, 2:3) give the model the vertical space it needs.
2. **Explicitly state "full body."** Don't assume the model knows. "A full-body photograph of..." makes your intent clear.
3. **Anchor the feet.** Describe what the person is standing on. "Standing on a polished concrete floor" or "bare feet on wet sand" grounds the figure.
4. **Describe the pose from head to toe.** The more you specify, the less the model improvises (and improvisation is where anatomy errors creep in).
5. **Use directional language.** "Facing the camera, weight shifted onto the left leg, right hand on hip."

### Aspect Ratios for Full Body

| Ratio | Resolution | When to Use |
|-------|-----------|-------------|
| **9:16** | 1080×1920 (1MP) / 1440×2560 (2MP) / 2048×3640 (4MP) | Social media, fashion lookbooks, maximum vertical space |
| **3:4** | 960×1280 (1MP) / 1344×1792 (2MP) / 1728×2304 (4MP) | Magazine editorials, portraits with environment |
| **2:3** | 904×1356 (1MP) / 1280×1920 (2MP) / 1632×2448 (4MP) | Classic portrait ratio, balanced composition |
| **1:1** | 1024×1024 (1MP) / 1440×1440 (2MP) / 2048×2048 (4MP) | Crop for full body — riskiest ratio for full figures |

### Full Body Prompt Examples

**Simple full body:**
```
A full-body photograph of a young man in a charcoal grey overcoat, standing 
on a wet city sidewalk at night, facing the camera with hands in his pockets. 
Streetlights create long shadows on the pavement behind him.
```

**Full body with pose detail:**
```
Full-body fashion shot of a woman in her late 20s with auburn hair in a low 
bun. She stands with her weight on her right leg, left knee slightly bent, 
wearing high-waisted wide-leg black trousers and a fitted emerald green 
turtleneck. Her left arm hangs naturally at her side, right hand holding a 
small structured handbag. Shot from a slightly low angle against a minimalist 
white studio backdrop. Soft directional lighting from the upper left.
```

**Full body action:**
```
A full-body shot of a male dancer mid-leap across a sunlit rehearsal studio, 
wearing loose white rehearsal clothes. His arms are extended upward, left leg 
stretched forward, right leg bent behind. Hard shadows from tall windows cast 
diagonal lines across the wooden floor. Shot at 1/500s shutter speed, freezing 
the motion with slight motion blur on the hands.
```

### Common Full Body Failure Modes

| Problem | Cause | Fix |
|---------|-------|-----|
| Feet cut off | Not enough vertical resolution | Use 3:4 or 9:16 ratio; explicitly mention feet/ground |
| Disproportionate limbs | Vague pose description | Describe each limb's position explicitly |
| Floating figure | No ground plane described | Add what they're standing on: "on a marble floor" |
| Head too small | Model compresses figure to fit | Use taller aspect ratio; specify "head at top of frame" |
| Blurry lower body | Model prioritizes face | Add detail to clothing below the waist |

---

## 5. Camera Shots & Framing Control

FLUX.2 has strong understanding of photographic terminology. Using real camera language gives you precise control over framing.

### Shot Types

| Shot Type | What It Shows | Example Prompt |
|-----------|--------------|----------------|
| **Extreme close-up** | Eyes, mouth, individual features | "Extreme close-up of a woman's eye, brown iris with golden flecks, visible lashes catching light" |
| **Close-up** | Face filling frame, shoulders visible | "Close-up portrait of a man's face, three-quarter view, dramatic side lighting" |
| **Medium close-up** | Head and upper chest | "Medium close-up of a woman speaking into a microphone, stage lights behind her" |
| **Medium shot** | Waist up | "Medium shot of a bartender shaking a cocktail, wearing a white dress shirt with sleeves rolled up" |
| **Medium full shot** | Knees up | "Medium full shot of a violinist performing, seated on a stool, sheet music on a stand" |
| **Full shot** | Entire body visible | "Full shot of a woman walking through a train station, pulling a rolling suitcase" |
| **Wide shot** | Full body + significant environment | "Wide shot of a lone figure standing at the edge of a desert canyon at sunset" |
| **Extreme wide** | Tiny figure in vast landscape | "Extreme wide shot of a rock climber on a cliff face, surrounded by mountain peaks" |

### Camera Angles

| Angle | Effect | When to Use |
|-------|--------|-------------|
| **Eye level** | Neutral, natural, documentary | Default for portraits and candid shots |
| **Slightly low** | Slight authority, flattering | Fashion, business portraits |
| **Low angle** | Power, dominance, heroic | Athletes, leaders, dramatic character shots |
| **High angle** | Vulnerability, smallness | Children, emotional moments |
| **Bird's eye** | Overhead, abstract, surveillance | Flat lay compositions, artistic |
| **Dutch angle** | Tension, unease, dynamism | Action, thriller, fashion editorial |
| **Worm's eye** | Extreme power, towering | Architecture with figures, dramatic skies |

### Lens Terminology

FLUX.2 understands focal length effects:

- **24mm** — Wide angle, environmental context, distortion at edges. Good for: street fashion, architecture with people.
- **35mm** — Slight wide angle, natural perspective. Good for: documentary, environmental portraits.
- **50mm** — "Normal" lens, closest to human eye. Good for: classic portraits, street photography.
- **85mm** — Short telephoto, beautiful bokeh, slight compression. Good for: headshots, beauty portraits.
- **135mm** — Telephoto, strong compression, creamy bokeh. Good for: fashion editorials, detail shots.
- **200mm** — Long telephoto, significant compression, subject isolation. Good for: sports, paparazzi, wildlife with people.

### Focusing & Depth of Field

```
Shot at f/1.4 with an 85mm lens, shallow depth of field, subject's eyes in 
sharp focus while the background dissolves into soft circular bokeh.
```

```
Deep focus shot at f/11, everything from the foreground figure to the 
mountains in the background is in crisp focus.
```

---

## 6. Controlling Poses & Body Language

FLUX.2's world knowledge means it has a much better understanding of how bodies work. Poses that would have been impossible in FLUX.1 are now achievable — but you still need to be specific.

### The Pose Description Framework

Describe poses using this order:

1. **Overall stance** — standing, sitting, leaning, crouching, lying
2. **Weight distribution** — which leg bears weight, how the body balances
3. **Torso orientation** — facing camera, turned away, three-quarter view
4. **Arm positions** — what each hand/arm is doing
5. **Head/face** — direction of gaze, expression, tilt
6. **Leg/foot positions** — crossed, apart, one forward

### Pose Examples by Complexity

**Simple (exploration):**
```
A woman leaning against a doorway, arms crossed, looking directly at the 
camera with a slight smirk.
```

**Moderate (most use cases):**
```
A man sitting on a park bench, turned slightly to the right, left ankle 
resting on his right knee, elbows on knees, chin resting on interlocked 
fingers, gazing thoughtfully into the middle distance.
```

**Complex (editorial/fashion):**
```
A woman in a dynamic walking pose captured mid-stride, right foot forward, 
left arm swinging back, right arm bent at the elbow with hand near her 
hip, head turned over her left shoulder looking back at the camera, wind 
catching her hair and the hem of her flowing dress. Shot from a low angle 
with a 35mm lens.
```

### Body Language Vocabulary

| Intention | Phrases |
|-----------|---------|
| **Confident** | "standing tall, shoulders back, chin raised, direct gaze" |
| **Relaxed** | "weight shifted to one hip, one hand in pocket, casual slouch" |
| **Tense** | "rigid posture, clenched jaw, shoulders hunched toward ears" |
| **Playful** | "tilted head, wide smile, one foot kicked up behind" |
| **Melancholic** | "slumped shoulders, downcast eyes, hands clasped in lap" |
| **Power** | "feet planted wide, arms akimbo, chest broad, looking down at camera" |
| **Vulnerable** | "arms wrapped around torso, looking away, hunched posture" |

### Common Pose Pitfalls

| Problem | Why It Happens | Fix |
|---------|---------------|-----|
| Symmetrical stiff pose | Default human pose in training data | Add asymmetry: "weight on left leg, right hand in pocket" |
| Both hands doing nothing | Unspecified limbs default to awkward rest positions | Give each hand a specific task or position |
| Impossible twist | Conflicting directional cues | Check consistency: if facing left, left shoulder should be forward |
| Sitting pose, legs wrong | Model doesn't know the chair depth | Describe legs: "knees at 90 degrees, feet flat on the floor" |
| Arms too short/long | Proportion drift with complex poses | Keep arm descriptions simple and near body |

---

## 7. Aspect Ratio & Resolution Strategies

FLUX.2 natively supports up to 4MP (2048×2048), a 4× increase over FLUX.1. This eliminates most upscaling workflows for people shots.

### Resolution Tiers

| Tier | Megapixels | Example Dimensions | Use Case |
|------|-----------|-------------------|----------|
| **1MP** | ~1.0 | 1024×1024, 768×1344, 1344×768 | Quick iteration, consumer GPUs, concept testing |
| **2MP** | ~1.8–2.0 | 1440×1440, 1216×1856, 1856×1216 | Standard quality, balanced speed/quality |
| **4MP** | ~3.5–4.0 | 2048×2048, 1536×2560, 2560×1536 | Final output, prints, professional work |

### Choosing Ratios for People

| Composition | Recommended Ratio | Resolution @ 2MP |
|-------------|-------------------|-----------------|
| Single portrait (head & shoulders) | 4:5 or 1:1 | 1184×1480 or 1440×1440 |
| Half body / bust | 3:4 or 4:5 | 1344×1792 or 1184×1480 |
| Full body (standing) | 2:3 or 9:16 | 1280×1920 or 1080×1920 |
| Full body (fashion runway) | 9:16 | 1080×1920 |
| Couple / two people | 3:4 | 1344×1792 |
| Group (3–5 people) | 16:9 or 3:2 | 1856×1040 or 1632×1088 |
| Large group / environmental | 16:9 | 1856×1040 |
| Wide cinematic scene with figure | 21:9 or 16:9 | 1920×824 or 1856×1040 |

### Resolution Strategy by Workflow

**Iteration phase:** Use 1MP with klein (9B). Fast, cheap, good enough to evaluate composition, pose, and style. Generate 20–50 variations in minutes.

**Refinement phase:** Use 2MP with dev (32B) or flex via API. Better detail, more anatomical accuracy. Generate 5–10 final candidates.

**Final output:** Use 4MP with pro or max. Maximum detail, perfect anatomy, no upscaling artifacts. Generate 2–3 selects.

### Output Resolution via API

For cloud API users, FLUX.2 supports flexible input/output ratios. You can specify any dimensions within the 4MP budget:

```
Output dimensions: 1536 × 2304 (3:4, ~3.5MP)
Output dimensions: 2048 × 2048 (1:1, 4MP)
Output dimensions: 1440 × 2560 (9:16, ~3.7MP)
Output dimensions: 2048 × 1152 (16:9, ~2.4MP)
```

---

## 8. Prompt Order & Layout Control

FLUX.2's Mistral VLM encoder processes prompts sequentially, paying more attention to earlier elements. This makes prompt order a powerful layout tool.

### Layout Hierarchy

The earlier a concept appears in your prompt, the more visual weight it gets. This is consistent and exploitable:

```
A woman standing in the center of the frame [SUBJECT - most prominent]
in a sunlit Parisian café [SETTING - prominent]
with a red bicycle leaning against the wall behind her [BACKGROUND - moderate]
and a tabby cat sleeping on the windowsill to the left [BACKGROUND DETAIL - least prominent]
```

### Controlling Spatial Layout

**Left/Right placement:**
```
A man standing on the left side of the frame, a vintage car parked on the 
right, shot in a wide garage.
```

**Foreground/Background layering:**
```
In the foreground, a woman's hands holding a coffee cup. In the background, 
slightly out of focus, a bustling farmer's market.
```

**Centering:**
```
A young girl standing in the center of a vast empty swimming pool, shot 
directly from above.
```

### Multi-Character Layout

For multiple people, describe them in spatial order (left to right, front to back):

```
A family of four on a beach at sunset. On the left, a father holding a 
toddler on his shoulders. In the center, a mother kneeling in the sand, 
arms open toward the camera. On the right, an older boy building a sandcastle. 
All facing the camera.
```

### Size and Prominence Control

To make one element larger than another, describe it first and in more detail:

```
A large golden retriever sitting in the foreground, filling the lower third 
of the frame, its fur catching warm afternoon light. Behind the dog, a 
smaller figure of a woman in a garden, out of focus.
```

---

## 9. Describing Body Types Realistically

FLUX.2 has improved world knowledge about human body diversity. Describe body types with specificity and respect.

### Effective Body Type Descriptions

| Instead of... | Use... |
|---------------|--------|
| "plus-size woman" | "a woman with a fuller figure, soft curves, wide hips" |
| "fit man" | "a muscular man with broad shoulders, defined arms, narrow waist" |
| "thin girl" | "a slender young woman with delicate collarbones and long limbs" |
| "average build" | "a person of medium height and average build" |
| "tall man" | "a man who stands over six feet tall, with long legs and a lean torso" |

### Age Description Tips

- Use specific age ranges: "in her early 30s," "a teenager around 16," "a man in his 60s"
- Describe age indicators: "laugh lines around her eyes," "silver hair at the temples," "smooth youthful skin"
- Avoid: "old lady," "old man" — specify the decade and characteristics

### Skin Tone Description

FLUX.2 handles diverse skin tones well. Describe using natural, specific language:

- "warm brown skin with golden undertones"
- "pale porcelain complexion with light freckles across the nose"
- "deep ebony skin with a warm, rich tone"
- "olive skin tanned from years of outdoor work"
- "fair skin with a rosy flush on the cheeks"

### Fitness and Physique

```
A female athlete with a swimmer's build — broad shoulders, powerful arms, 
and a strong core — standing poolside in a racing swimsuit.
```

```
A middle-aged man with a dad-bod physique, soft midsection, thick arms 
from years of manual labor, wearing a plaid flannel shirt.
```

---

## 10. Hands, Limbs & Anatomy Pitfalls

FLUX.2 achieves a ~30% reduction in hand and finger errors compared to FLUX.1, and skin texture, fabric detail, and subsurface scattering are all notably improved. But hands remain the hardest part of human anatomy to generate.

### What FLUX.2 Does Better

- **Finger count:** Significantly improved. Most generations now produce the correct number of fingers, especially in simpler hand poses.
- **Skin texture:** Realistic pores, veins, wrinkles, and subsurface scattering.
- **Joint bending:** Fingers bend in plausible directions more often.
- **Proportion:** Limb lengths relative to the body are more accurate.

### Hands: Best Practices

**Keep hands simple:**
```
[Yes] "Her right hand rests on her hip, fingers relaxed."
[Yes] "Both hands clasped together in front of her waist."
[Yes] "Her left hand holds a coffee cup, fingers wrapped around it."
```

**Avoid complex hand interactions:**
```
[No] "She's playing piano with both hands, fingers on individual keys."
[No] "Her hands are braiding her hair with precise finger movements."
[No] "He's counting on his fingers with specific digits extended."
```

**If you need detailed hands:**
- Use a close-up or medium close-up framing (less of the body to get right)
- Describe the hand position relative to an object (the object acts as an anchor)
- Generate multiple times and select — hands are still the most variable element

### Common Anatomy Issues and Fixes

| Issue | Frequency | Fix |
|-------|-----------|-----|
| Extra fingers | Reduced ~30% vs FLUX.1 | Keep hand poses simple; use close-ups; describe hand around object |
| Merged fingers | Occasional | Describe fingers as "spread apart" or "relaxed and separate" |
| Wrong limb count | Rare in pro/max, more common in klein | Describe limb positions explicitly |
| Asymmetric limb lengths | Occasional in full body | Use taller aspect ratios; describe proportions |
| Shoulder asymmetry | Occasional | Describe shoulder position: "broad, level shoulders" |
| Neck too long/short | Occasional | Describe explicitly if important: "a natural-length neck" |
| Feet distortion | Common in full body | Use 9:16 ratio; describe footwear or "bare feet on [surface]" |

### The Hand-on-Object Trick

The single most effective technique for getting good hands is to put something in them:

```
Instead of: "A woman standing with her hands at her sides."
Use:       "A woman holding a leather tote bag by its handles in her right hand, 
            left hand adjusting her sunglasses."
```

The object constrains the hand shape and gives the model a reference point.

---

## 11. Multi-Reference Image Support for Character Consistency

This is one of the most significant new features in FLUX.2 and a game-changer for people photography workflows.

### How It Works

FLUX.2 supports up to **10 reference images** that can guide the generation. References can control:

- **Character consistency** — Same face, body type, and styling across multiple images
- **Product consistency** — Same clothing, accessories, or props
- **Style transfer** — Match the artistic style of a reference image
- **Pose guidance** — Reference a pose image for body positioning

### Character Consistency Workflow

1. Generate your initial character portrait with a detailed prompt.
2. Save the image you like best as a reference.
3. Upload it as a reference image in subsequent prompts.
4. Describe the new pose/scene while the reference maintains identity.

**Initial character:**
```
A portrait of a 28-year-old woman with copper-red curly hair past her shoulders, 
green eyes, light freckles, and a slender build. She wears minimal makeup — 
just mascara and a nude lip. Shot against a neutral grey backdrop with soft 
rembrandt lighting.
```

**Consistent character, new scene:**
```
[Reference: initial portrait]
The same woman from the reference, now standing on a rooftop terrace at dusk, 
wearing a dark teal wrap dress. She faces away from the camera, looking out 
over city lights, one hand resting on the railing.
```

### Tips for Multi-Reference

- **Use 1–3 references** for character consistency. More references spread the model's attention thinner.
- **Match lighting** between reference and target when possible — dramatically different lighting can cause identity drift.
- **Reference quality matters.** The cleaner the reference image, the better the consistency.
- **For outfit changes,** include a reference of the new outfit or describe it in precise detail alongside the character reference.
- **Multiple characters:** Use separate references for each character, clearly described in the prompt.

### Limitations

- Multi-reference is available via API (pro/max/flex). Local dev users need community workarounds (e.g., IP-Adapter for FLUX.2).
- Klein does not natively support multi-reference via the base model — requires adapter workflows.
- Extreme angle changes (profile reference → straight-on generation) may lose some consistency.

---

## 12. Using ControlNet with FLUX.2

ControlNet remains the gold standard for precise pose control. FLUX.2 has compatible ControlNet adapters for both dev and klein.

### Available ControlNet Models for FLUX.2

| ControlNet | Compatible With | Source | Best For |
|-----------|----------------|--------|----------|
| **Qwen2VL-Flux ControlNet** | FLUX Dev (and 2-dev with adaptation) | HuggingFace (Nov 2024+) | Depth, Canny, OpenPose |
| **FLUX.2-dev-Fun-Controlnet-Union** | FLUX.2 dev | HuggingFace | Depth, Canny, OpenPose, Pose |
| **Klein Depth ControlNet** | FLUX.2 klein | Civitai | Depth-based pose control |
| **Klein OpenPose ControlNet** | FLUX.2 klein | Civitai | Skeleton-based pose control |

### Recommended ControlNet Settings

These principles carry over from FLUX.1 and remain effective:

| Setting | Recommended Range | Notes |
|---------|-------------------|-------|
| **Strength** | 0.4–0.7 | Start at 0.5. Lower = more creative freedom. Higher = stricter adherence. |
| **Start percent** | 0.0 | ControlNet guides from the beginning of generation |
| **End percent** | 0.3–0.5 | Stop controlling early — let the model refine details freely |
| **Preprocessor resolution** | Match generation resolution | Mismatched resolutions cause layout distortion |

### When to Use Each ControlNet Type

| Preprocessor | Use When | Avoid When |
|-------------|----------|-----------|
| **OpenPose** | You need specific limb positions, dance poses, gesture control | You want the model to interpret the pose naturally |
| **Depth** | You want to control composition/depth while letting the model handle details | You need specific limb angles |
| **Canny** | You want to transfer a specific outline (e.g., sketch to photo) | Your reference has noise or unwanted edges |

### ControlNet + Prompt Combination

ControlNet handles *structure*, your prompt handles *content*. Don't duplicate information:

```
ControlNet: OpenPose of a person sitting cross-legged, right hand raised

Prompt: "A young woman meditating on a yoga mat in a sunlit studio, wearing 
a lavender tank top and black leggings, serene expression, soft natural 
lighting from a large window."
```

Notice the prompt does NOT say "sitting cross-legged" — ControlNet handles that. The prompt describes everything else.

### ControlNet for Klein Users

Klein ControlNet workflows are available on Civitai and work similarly but with caveats:

- Depth ControlNet is more reliable than OpenPose for klein
- Lower strength values (0.3–0.5) are recommended due to klein's lower baseline anatomy quality
- Use ControlNet as a *corrective* tool rather than a *creative* tool with klein

---

## 13. FLUX.2 Structured Prompts & JSON Templates

FLUX.2's improved language understanding means JSON-structured prompts work even better than in FLUX.1. The model can parse complex nested structures with high fidelity.

### When to Use JSON vs. Natural Language

| Approach | Best For | Trade-off |
|----------|---------|-----------|
| **Natural language** | Quick iteration, creative exploration, most users | Slightly less precise for multi-element control |
| **JSON structured** | Production pipelines, consistent formatting, multi-element scenes | More verbose, requires more setup |
| **Hybrid** (JSON + natural language) | Complex scenes with precise color/style requirements | Most powerful, most verbose |

### JSON Template for People Shots

```json
{
  "subject": {
    "identity": "A 40-year-old Japanese man with short grey hair and glasses",
    "body_type": "medium build, slightly soft around the middle",
    "expression": "warm, gentle smile with crinkling eyes",
    "wardrobe": {
      "clothing": "dark indigo denim jacket over a cream cable-knit sweater",
      "footwear": "brown leather Chelsea boots",
      "accessories": "a vintage mechanical watch on his left wrist, thin silver chain necklace"
    }
  },
  "pose": "seated in a leather armchair, right ankle crossed over left knee, 
           left hand resting on the armrest, holding a hardcover book open on his lap",
  "environment": {
    "setting": "a cozy wood-paneled study with floor-to-ceiling bookshelves",
    "lighting": "warm amber light from a brass desk lamp and a crackling fireplace to the right",
    "background_details": "a sleeping golden retriever on a rug near the fireplace, 
                          a cup of tea on a side table"
  },
  "camera": {
    "shot_type": "medium shot",
    "angle": "slightly low, at eye level with the subject",
    "lens": "50mm, f/2.0",
    "film_stock": "Kodak Portra 400"
  },
  "style": "intimate portrait photography, warm color palette, shallow depth of field"
}
```

### New FLUX.2 JSON Fields

These fields work in FLUX.2 but were unreliable in FLUX.1:

| Field | Purpose | Example |
|-------|---------|---------|
| `Cinematic_Tone_And_Lighting_Key` | Specify a cinematic lighting mood | `"golden hour backlit silhouette"` |
| `detail_preservation` | Request specific detail levels | `"high"` or `"ultra-fine fabric detail"` |
| `color_match` | Lock colors to specific values | `"exact"` (pair with hex codes) |
| `hex_colors` | Assign specific colors to objects | See Section 16 |

### Multi-Person JSON Template

```json
{
  "scene": "A candid moment at a Sunday farmer's market",
  "characters": [
    {
      "position": "foreground left",
      "description": "A woman in her 30s with box braids, wearing a straw hat and 
                      white linen dress, carrying a basket of peaches, smiling at 
                      someone off-camera"
    },
    {
      "position": "midground right",
      "description": "An elderly man in overalls behind a vegetable stand, arranging 
                      tomatoes, looking content"
    }
  ],
  "environment": {
    "setting": "outdoor market with white tents, dappled sunlight through oak trees",
    "time_of_day": "mid-morning, soft warm light",
    "atmosphere": "lively but not crowded, peaceful"
  },
  "camera": {
    "shot_type": "medium wide shot",
    "lens": "35mm, f/2.8",
    "style": "photojournalistic, candid, natural light only"
  }
}
```

---

## 14. Negative Prompts & Common Fixes

### Negative Prompts by Variant

| Variant | Negative Prompts Supported? | Behavior |
|---------|---------------------------|----------|
| **pro** | [No] | Describe everything positively in your prompt |
| **max** | [No] | Describe everything positively in your prompt |
| **flex** | [No] | Describe everything positively in your prompt |
| **dev** | [Yes] (via ComfyUI) | Acts as creative exclusion, not a quality filter |
| **klein** | [Yes] (via ComfyUI) | Acts as creative exclusion, not a quality filter |

### For pro/max/flex Users: The "Positive Only" Approach

Since you can't use negative prompts, you must describe what you *want* rather than what you *don't want*:

| Instead of... | Use... |
|---------------|--------|
| `"no extra fingers"` | `"her right hand rests naturally on the table, fingers relaxed and together"` |
| `"no blurry background"` | `"the entire scene is in sharp focus from foreground to background"` |
| `"not deformed"` | (don't mention — just write a good prompt) |
| `"no text, no watermark"` | (unnecessary — FLUX.2 doesn't add watermarks) |
| `"not cartoon, photorealistic"` | (unnecessary — photorealism is the default) |

### For dev/klein Users: Effective Negative Prompts

Negative prompts in FLUX.2 dev/klein are *creative exclusions*, not quality enhancers. Use them to steer the model away from unwanted creative directions:

**Effective negatives:**
```
jewelry, watch, necklace, bracelet, earrings, ring
```
*(When you want no accessories at all)*

```
sitting, crouching, kneeling
```
*(When you specifically want a standing pose and the model keeps sitting)*

```
smiling, grinning, laughing
```
*(When you want a serious/neutral expression)*

**Ineffective negatives (don't bother):**
```
ugly, bad quality, low res, deformed, blurry, extra fingers
```
*(These are quality filters, not creative exclusions — FLUX.2 doesn't respond to them)*

### Common Fixes Without Negative Prompts

| Problem | Positive-Only Fix |
|---------|-------------------|
| Unwanted jewelry/accessories | Describe bare wrists/hands/neck explicitly |
| Wrong expression | Describe desired expression in detail |
| Unwanted background elements | Specify background explicitly and fully |
| Clothing changing between generations | Describe clothing in precise detail (fabric, cut, color) |
| Wrong hair style | Describe hair in detail (length, texture, style, color, parting) |

---

## 15. FLUX.2 Klein: Anatomy Fixes for Local Users

FLUX.2 klein (9B and 4B) is Apache 2.0 and runs on consumer GPUs, making it the most accessible variant. However, it has **significantly more anatomy issues** than the larger variants. This section provides critical tuning advice.

### The Klein Anatomy Problem

Klein's smaller parameter count means:
- Extra limbs appear more frequently
- Hands are less reliable (more fused/extra fingers)
- Sitting poses are notably worse than standing poses
- Complex poses (twisting, reaching) often produce artifacts
- Face quality can drop when body complexity increases

### Critical Settings for Better Anatomy

| Setting | Default | Recommended for Anatomy | Notes |
|---------|---------|------------------------|-------|
| **Steps** | 4 | 6–8 | 4 steps often causes anatomy issues. 6 is a good starting point. |
| **CFG / Guidance** | 1.0 | 1.2–1.5 | Slightly above 1 helps finger issues. Going above 1.5 re-introduces problems. |
| **Sampler** | euler | euler, or **res_2s** | res_2s (2 model calls per step) can fix anatomy at lower step counts |
| **Resolution** | — | 1MP | Higher resolutions amplify anatomy errors |
| **Seed** | Random | Try multiple | Anatomy quality varies significantly between seeds |

### The Steps Tradeoff

With klein, more steps isn't always better:

| Steps | Quality | Speed | Risk |
|-------|---------|-------|------|
| **4** | Fast but anatomy errors common | Sub-second | Extra limbs, melted hands, sitting failures |
| **6** | Good balance | ~2–3 seconds | Best starting point for most users |
| **8** | Best anatomy accuracy | ~4–5 seconds | Can introduce new issues that 6 didn't have |
| **10+** | Diminishing returns | 6+ seconds | Sometimes worse than 8 due to overfitting |

**Recommendation:** Start at 6 steps. If anatomy is bad, try 8. If 8 introduces new problems, go back to 6 and adjust CFG instead.

### The res_2s Sampler Trick

The `res_2s` sampler runs the model **twice per step**, effectively doubling the computation at each step. This can fix anatomy issues at lower step counts:

```
4 steps with res_2s ≈ quality of 6–8 steps with euler, at similar speed
```

Try this workflow:
1. Generate at 4 steps, euler → check anatomy
2. If bad, switch to res_2s at 4 steps → check again
3. If still bad, try euler at 6–8 steps

### CFG Tuning for Klein

| CFG Value | Effect |
|-----------|--------|
| **1.0** (default) | Fastest, most "creative," most anatomy issues |
| **1.1–1.2** | Slight improvement in finger count and limb accuracy |
| **1.3–1.5** | Sweet spot for most anatomy issues |
| **1.6–2.0** | Risk zone — can cause color burning, over-sharpening, and *new* artifacts |
| **2.0+** | Bad — heavily distorted, do not use |

### Klein Pose Strategy

Since klein struggles with complex poses:

1. **Prefer standing poses.** They are dramatically more reliable than sitting, crouching, or lying poses.
2. **Keep poses symmetrical or near-symmetrical.** Weight on both feet, arms at sides or in similar positions.
3. **Use ControlNet (Depth or OpenPose)** to guide anatomy — see [Section 12](#12-using-controlnet-with-flux2).
4. **Describe limbs simply.** "Arms at her sides" is more reliable than "left arm bent at the elbow with hand touching the back of her neck while right arm extends forward holding a..."
5. **Generate more candidates.** Klein's anatomy varies widely between seeds. Generate 8–16 variations and pick the best.

### When to Switch to dev

If you're consistently frustrated with klein anatomy, consider moving to **dev (32B, FP8)** which requires ~18GB for 1MP generation. Dev's anatomy quality is significantly better and is often worth the speed tradeoff for final-quality images.

---

## 16. Hex Color Prompting for Clothing & Outfit Control

FLUX.2 natively supports hex color codes, allowing you to assign precise colors to specific objects. This is particularly powerful for clothing and outfit control.

### How Hex Colors Work

Simply include hex codes in your prompt alongside the object description:

```
A woman wearing a #2C3E50 navy blazer over a #F5E6CC cream silk blouse, 
with #8B4513 brown leather ankle boots.
```

FLUX.2 will match the specified colors to the described objects with high accuracy.

### Color Control Methods Compared

| Method | Precision | Best For |
|--------|-----------|----------|
| **Named colors** ("navy blue") | Moderate | Quick descriptions, approximate colors |
| **Descriptive colors** ("midnight blue with a slight purple undertone") | High | Artistic color direction |
| **Hex codes** ("#1B1464") | Very High | Exact brand colors, design systems, product shots |
| **Named + hex** ("navy blue #1B1464") | Very High + fallback | Most reliable approach |

### Clothing Control Prompt Template

```
A [subject description] wearing:
- a [garment type] in [color/hex], [fabric/detail]
- [second garment] in [color/hex], [fabric/detail]  
- [footwear] in [color/hex]

Additional: [accessories with colors]
```

### Practical Examples

**Brand-accurate outfit:**
```
A professional woman standing in a modern office lobby, wearing a 
#1B2A4A dark navy tailored blazer, a #FFFFFF crisp white button-down shirt, 
#2F4F4F dark slate grey tailored trousers, and #5C4033 dark brown pointed-toe 
pumps. A thin #C0C0C0 silver watch on her left wrist.
```

**Color palette control:**
```
A man at an outdoor café wearing an autumn color palette: a #CC5500 burnt orange 
wool sweater, #556B2F dark olive green corduroy trousers, #8B6914 dark golden 
brown suede boots. A #DAA520 goldenrod scarf draped loosely around his neck.
```

### JSON with Hex Colors

For maximum control, combine JSON structure with hex codes:

```json
{
  "subject": {
    "identity": "A woman in her 30s with blonde hair in a low chignon",
    "wardrobe": {
      "dress": {
        "type": "knee-length wrap dress",
        "color": "#7B2D8E",
        "color_name": "deep plum purple",
        "fabric": "heavy silk crepe"
      },
      "shoes": {
        "type": "slingback heels",
        "color": "#2C2C2C",
        "color_name": "near-black"
      },
      "color_match": "exact"
    }
  }
}
```

### Tips for Color Control

- Use `color_match: "exact"` in JSON to force precise color adherence.
- Combining hex code + color name is the most reliable approach: `"#7B2D8E deep plum purple"`.
- FLUX.2 handles metallic colors well: `"#C9B037 antique gold metallic finish"`.
- For patterns, describe the pattern separately from the base color: `"a #1C3A5F navy blue and #FFFFFF white striped button-down shirt"`.
- Lighting affects perceived color. If exact color is critical, describe neutral lighting: `"under even studio lighting with accurate white balance"`.

---

## 17. Copy-Paste Prompt Templates

All templates are designed for FLUX.2. Quality tags have been removed. Camera-specific details are included for precision.

### Portrait Templates

**Clean headshot:**
```
A professional headshot of [AGE] [GENDER] with [HAIR DESCRIPTION], [SKIN DESCRIPTION], 
and [EXPRESSION]. Shot with an 85mm lens at f/2.0 against a [COLOR] seamless backdrop. 
Soft even lighting from a large softbox at 45 degrees. Eyes sharp, slight catchlight visible.
```

**Environmental portrait:**
```
An environmental portrait of [SUBJECT], [AGE], [PHYSICAL DESCRIPTION], 
[DOING ACTION] at [LOCATION]. Shot with a 50mm lens at f/2.8. 
[TIME OF DAY] natural light. [BACKGROUND DETAILS]. Shallow depth of field 
separating the subject from the environment.
```

**Character portrait (for consistency reference):**
```
A detailed character portrait of [NAME/DESCRIPTION], [AGE], [HAIR: color, length, texture, style], 
[EYE COLOR], [SKIN TONE], [BUILD], [DISTINGUISHING FEATURES]. 
Neutral expression, facing the camera. Even studio lighting against a grey backdrop. 
No accessories. Shot with a 105mm macro lens at f/5.6 for uniform sharpness across the face.
```

### Fashion Templates

**Editorial fashion full body:**
```
Full-body fashion editorial shot of [SUBJECT], [BUILD], [POSE]. 
Wearing [DETAILED OUTFIT DESCRIPTION including colors/fabrics]. 
[LOCATION/BACKDROP]. Shot from a [LOW/EYE/HIGH] angle with a [MM]mm lens at f/[APERTURE]. 
[LIGHTING DESCRIPTION]. [STYLING: hair, makeup]. Vogue editorial style, cinematic color grading.
```

**Street style:**
```
A candid street style photograph of [SUBJECT], [BUILD], walking down [STREET DESCRIPTION]. 
Wearing [OUTFIT]. Shot from a slightly low angle with a 35mm lens at f/2.8. 
[TIME OF DAY], [WEATHER/ATMOSPHERE]. Motion blur on passing elements in the background.
```

### Cinematic Templates

**Scene from a film:**
```
A cinematic still from a [GENRE] film. [SUBJECT], [POSE/EXPRESSION], 
[LOCATION DESCRIPTION]. [TIME OF DAY/WEATHER]. 
Shot with an [MM]mm anamorphic lens, f/[APERTURE]. 
[CINEMATIC_LIGHTING: e.g., motivated practical lighting, rim light from behind, 
orange-blue color contrast]. Shallow depth of field, slight film grain, letterboxed composition.
```

**Dramatic lighting portrait:**
```
A portrait of [SUBJECT] lit by a single [LIGHT_SOURCE: e.g., desk lamp, candle, phone screen], 
creating strong [DIRECTION: e.g., chiaroscuro, Rembrandt, split] lighting across their face. 
The rest of the scene falls into deep shadow. Shot with a 85mm lens at f/1.4. 
[EXPRESSION]. Moody atmosphere, high contrast.
```

### Group / Multi-Character Templates

**Couple:**
```
A photograph of a couple, [DESCRIPTION OF PERSON 1] and [DESCRIPTION OF PERSON 2], 
[POSE/INTERACTION]. [LOCATION/SETTING]. [LIGHTING]. Shot with a 50mm lens at f/2.0. 
Both faces in focus, background softly blurred. [MOOD/ATMOSPHERE].
```

**Professional team:**
```
A group portrait of [NUMBER] professionals, [BRIEF DESCRIPTIONS OR POSITIONS], 
arranged in [FORMATION: e.g., a loose semi-circle, two rows]. 
[SETTING: e.g., modern office lobby, outdoor campus]. 
Even corporate lighting from two softboxes. Shot with a 35mm lens at f/5.6 for 
consistent focus across the group. Business casual attire.
```

### Action / Sports Templates

**Athlete in motion:**
```
A sports photograph of [ATHLETE], [SPORT], captured at the peak of [ACTION]. 
[PHYSICAL DESCRIPTION]. Wearing [UNIFORM/GEAR]. Shot with a 200mm lens at f/2.8, 
1/1000s shutter speed freezing the action. [SETTING: e.g., stadium, court, track]. 
[Intense/focused/determined] expression. Backlit by stadium lights, slight rim glow.
```

### Klein-Optimized Templates

These templates are simplified for FLUX.2 klein to minimize anatomy issues:

**Simple standing portrait (klein):**
```
A standing portrait of [SUBJECT], [AGE], [BUILD], [HAIR DESCRIPTION]. 
Facing the camera with a neutral expression. Arms at sides. 
Wearing [SIMPLE OUTFIT: one or two pieces]. 
[BACKGROUND]. Even lighting. Shot with an 85mm lens.
```

**Simple standing full body (klein):**
```
Full-body photograph of [SUBJECT], [BUILD]. Standing facing the camera with 
weight evenly distributed on both feet, arms relaxed at sides. 
Wearing [OUTFIT DESCRIPTION]. On a [FLOOR/SURFACE]. 
[BACKGROUND]. Soft natural lighting. Clean composition.
```

> **Note:** For klein, keep poses simple and symmetrical. Avoid crossed legs, hands behind the head, or complex arm positions.

---

## 18. Quick Troubleshooting Cheat Sheet

### The Diagnosis Flowchart

```
Is the problem structural (extra limbs, wrong proportions)?
├── YES → Check aspect ratio (use taller for full body)
│         → Simplify pose description
│         → Try ControlNet (Depth or OpenPose)
│         → If using klein: increase steps to 6-8, try res_2s, CFG 1.3
│         → Generate multiple seeds and select best
│
└── NO → Is the problem stylistic (wrong colors, style, mood)?
          → Check prompt order (most important elements first)
          → Use hex codes for exact colors
          → Add specific lighting description
          → Describe the style reference explicitly
```

### Common Problems & Solutions

| Problem | Likely Cause | Solution |
|---------|-------------|----------|
| **Extra fingers** | Complex hand pose | Simplify hand position; put object in hand; use close-up framing |
| **Extra limbs** | Overly complex pose | Simplify pose to symmetrical stance; reduce number of described actions |
| **Floating figure** | No ground plane | Add "standing on [surface]" or "feet on [material]" |
| **Wrong expression** | Model defaults to smile | Describe expression in detail; use negative "smiling" (dev/klein only) |
| **Clothing morphed/wrong** | Vague clothing description | Specify fabric, cut, color (use hex), and how it fits the body |
| **Colors bleeding** | Attribute mixing between objects | Describe each object fully; use hex codes with `color_match: "exact"` |
| **Face looks different across generations** | No consistency reference | Use multi-reference feature with character portrait as reference |
| **Head too small / too large** | Wrong aspect ratio for framing | Adjust ratio: closer crop for portrait, taller ratio for full body |
| **Blurry output** | Wrong resolution or model issue | Check output resolution; for klein, verify quantization quality |
| **Hands behind head / hidden hands look wrong** | Hidden hands default to bad anatomy | Keep all hands visible or explicitly describe what they're doing |
| **Sitting pose is mangled (klein)** | Klein struggles with sitting | Switch to standing pose; if sitting is essential, use ControlNet Depth |
| **Anatomy worse at higher step count** | Klein overfitting at high steps | Reduce steps back to 6; try res_2s at 4 steps instead |
| **Text on clothing is gibberish** | Even FLUX.2 can't spell perfectly | Use flex variant for text rendering; keep text short (3-5 characters) |
| **Background doesn't match description** | Background described too late in prompt | Move background description earlier; increase prompt detail for setting |
| **Same prompt, different results** | Stochastic generation | Use seed locking for consistent results; use character reference for identity |

### Model-Specific Troubleshooting

| If using... | Common Issue | Quick Fix |
|-------------|-------------|-----------|
| **pro/max** | Can't remove unwanted elements | Describe desired scene fully; use layout ordering |
| **flex** | Slightly different aesthetic than pro | Adjust guidance scale; test step counts |
| **dev** | Slower generation, VRAM issues | Use FP8 quantization; reduce resolution to 1MP |
| **klein** | Anatomy issues | Steps: 6–8, CFG: 1.3, simple poses, multiple seeds |
| **klein 4B** | Quality significantly lower | Use 9B instead if GPU allows; 4B for drafts only |

### The "Generate 8, Pick 1" Rule

For any people shot where anatomy matters, **generate at least 8 variations** and select the best. This is especially critical for:

- Klein (anatomy varies wildly between seeds)
- Full body shots (limb proportion varies)
- Complex poses (hand/arm positions vary)
- Multi-character scenes (relationships between figures vary)

---

## 19. Sources

- **Black Forest Labs (BFL)** — Official FLUX.2 documentation, model cards, and API reference. [blackforestlabs.ai](https://blackforestlabs.ai)
- **BFL Official Prompting Guide** — Priority order framework, prompt length recommendations, word order guidance.
- **HuggingFace Model Cards** — FLUX.2-dev, FLUX.2-klein, and FLUX.2-dev-Fun-Controlnet-Union model pages with technical specifications.
- **Civitai Community** — Klein ControlNet workflows, community prompt testing results, anatomy fix guides.
- **Qwen2VL-Flux ControlNet** — HuggingFace page and documentation for Depth/Canny/OpenPose control with FLUX Dev variants.
- **FLUX.1 Prompting Guide (January 2025)** — Original guide that this document updates. Core principles of camera terminology, pose description, and full-body technique carried forward where still applicable.
- **Community testing reports** — Aggregated findings from FLUX.2 early access users on anatomy improvements, attribute mixing reduction, text rendering accuracy, and multi-reference capabilities.

---

*This guide is a living document. FLUX.2's capabilities continue to evolve with model updates and community discoveries. Last verified: May 7, 2026.*
