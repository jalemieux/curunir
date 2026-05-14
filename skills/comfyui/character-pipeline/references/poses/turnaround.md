---
name: turnaround
description: 2-side flank views to complement the front+back references — completes a LoRA training set
poses: 2
---

# Turnaround

Two flank views (left side, right side). Pairs with the locked front
and back **reference images** from Stage 2 of the character pipeline,
which already cover the head-on and rear angles. Sweeping these two
sides plus the two refs gives a full orthogonal turnaround for LoRA
training without re-rolling the front and back.

Use this when the goal is a character LoRA and nothing fancier.

## Poses

### side-left
full side profile facing camera-left, standing on a plain studio floor with weight evenly distributed, arms relaxed at sides

### side-right
full side profile facing camera-right, standing on a plain studio floor with weight evenly distributed, arms relaxed at sides
