---
name: gtm-reassess
description: Use when new information arrives (competitive move, pricing change, feature change, market shift) that may affect existing GTM documents. Reads completed phase docs, researches the full scope of the new intel, presents a per-section impact assessment with cascade analysis, then updates docs after builder approval. Trigger: builder brings new information that could change product-context, positioning, or gtm-plan.
---

# GTM Reassess

Evaluate new information against existing GTM documents, present an impact assessment to the builder, then apply approved changes to versioned copies.

**Requires:** Same research tools as the GTM pipeline — web-search, gemini-search, xai-search, reddit-research, linkedin-research, playwright.

## Inputs

The builder provides:
1. **New information** — what changed (competitive move, pricing shift, feature launch, market event). Can be a URL, a paste, or a verbal description.
2. **Product directory** — where the existing GTM docs live. Look for files matching `product-context*.md`, `positioning*.md`, `gtm-plan*.md` in the working directory and `test-runs/` directory.

## Workflow

### Step 1 — Intake & Discovery

1. Accept the new information from the builder.
2. Scan for existing GTM docs. Identify which phases have been completed:
   - **Phase 1:** `product-context.{product}.md` or `product-context.md`
   - **Phase 2:** `positioning.{product}.md` or `positioning.md`
   - **Phase 3:** `gtm-plan.{product}.md` or `gtm-plan.md`
   - Check both working directory and `test-runs/` directory.
3. Report what you found: "Found Phase 1 + 2 docs for {product}. Phase 3 not yet completed."

### Step 2 — Research the New Information

Don't take the builder's summary at face value. Research the full scope:

- If the builder provided a **URL**: fetch and extract the full content.
- **Search for context**: use web-search, gemini-search, xai-search to understand the broader implications. What does this mean for the market? How are users reacting? What's the pricing? What's the timeline?
- **Check community sentiment**: use reddit-research, xai-search to find how target buyers are reacting to this news.
- **Compile a research brief** (internal, not written to file) covering:
  - What exactly happened
  - Who it affects
  - How it changes the competitive landscape
  - What the target audience is saying about it
  - Timeline and availability

### Step 3 — Per-Section Impact Assessment

Read each existing GTM doc. For every section, evaluate impact against the research brief.

**Impact ratings:**

| Rating | Meaning | Action |
|--------|---------|--------|
| **Unchanged** | New info doesn't affect this section | None |
| **Minor Update** | Factual correction or addition needed, direction holds | Edit in place |
| **Major Revision** | Section conclusions change, downstream sections affected | Rewrite section |
| **Invalidated** | Core premise broken, needs re-research from scratch | Re-run phase skill |

**Phase 1 sections to assess:**
- Product Summary
- Target Buyer
- Competitive Landscape
- Market Positioning
- Buyer Language
- Gaps & Opportunities
- Pricing Intelligence

**Phase 2 sections to assess (per ICP):**
- ICP Priority / Selection
- ICP Profile
- Strategic Messaging (positioning statement, key angles, proof points, objection handling)
- Tactical Messaging (headlines, email subjects, elevator pitch)
- Pricing Signals

**Phase 3 sections to assess (per ICP):**
- Channel Inventory
- Builder Constraints
- Channel Strategy & Sequencing
- Per-Channel Plans (targeting, messaging variants, budget, metrics)
- Success Metrics & Decision Points

### Step 4 — Cascade Analysis

Map how upstream impacts flow downstream. Use this cascade map:

```
Phase 1 Section              → Phase 2 Impact                    → Phase 3 Impact
─────────────────────────────────────────────────────────────────────────────────
Competitive Landscape        → Positioning statements,           → Channel messaging,
                               objection handling, key angles      ad variants, Reddit strategy
Target Buyer                 → ICP selection, profiles            → Channel targeting
Market Positioning           → Strategic messaging,               → All channel plans
                               tactical messaging
Pricing Intelligence         → Pricing signals                    → Pricing narrative in channels
Gaps & Opportunities         → Key angles, proof points           → Channel strategy
Buyer Language               → Tactical messaging                 → Ad copy, email subjects
```

If a Phase 1 section is **Major Revision** or **Invalidated**, every downstream section it maps to is **at minimum** a Minor Update, often a Major Revision.

### Step 5 — Present Assessment to Builder (CHECKPOINT)

**STOP HERE. Do not modify any documents until the builder responds.**

Present the assessment as a table per phase, then a summary verdict:

```markdown
## Impact Assessment: {New Information Summary}

### Phase 1 — Product Context
| Section | Rating | Summary |
|---------|--------|---------|
| Product Summary | Unchanged | ... |
| Competitive Landscape | Major Revision | {competitor} now offers {feature}, directly overlapping core value prop |
| ... | ... | ... |

### Phase 2 — Positioning
| Section | Rating | Summary |
|---------|--------|---------|
| ICP 1 — Strategic Messaging | Major Revision | Positioning statement built on differentiation that no longer holds |
| ... | ... | ... |

### Phase 3 — GTM Plan
| Section | Rating | Summary |
|---------|--------|---------|
| Channel: X Ads — Variant D | Invalidated | Copy directly references competitor weakness that no longer exists |
| ... | ... | ... |

### Cascade Summary
{Diagram showing which Phase 1 changes drove which Phase 2/3 impacts}

### Verdict
- **Minimum action:** {what must change for the plan to not be misleading}
- **Recommended action:** {what should change for the plan to be competitive}
- **Nuclear option:** {when the right answer might be to re-run a phase or abandon the plan}
```

Then ask: **"How would you like to proceed? I can apply specific updates, re-run a phase, or you can tell me what to change."**

### Step 6 — Apply Updates

After the builder responds, apply their direction:

1. **Version the output** — never overwrite the original. Create versioned copies:
   - Original: `product-context.{product}.0.md` (or whatever exists)
   - Updated: `product-context.{product}.1.md` (increment the highest existing version)
   - Pattern: `{doc-name}.{product}.{version}.md`
2. **Apply changes section by section** — for sections rated Minor Update or Major Revision, rewrite the section content while preserving the document structure and all unchanged sections.
3. **Add a changelog header** to the versioned doc:
   ```markdown
   > **Reassessment v{N}** — {date}
   > **Trigger:** {one-line summary of new information}
   > **Changes:** {list of sections modified and why}
   ```
4. **For Invalidated sections** — if the builder wants a full re-run, advise them to invoke the appropriate phase skill (`gtm-onboard-ingest`, `gtm-position-segment`, or `gtm-plan`) with the updated upstream doc as input.

### Step 7 — Cascade Forward

If Phase 1 was updated and Phase 2 exists, automatically assess Phase 2 against the *updated* Phase 1 (not the original). Same for Phase 2 → Phase 3.

Present any new cascade impacts to the builder before applying them. This is another checkpoint — the builder may decide to stop here.

## Tips

- **Don't minimize impact.** If a competitor ships your core feature, say so. The builder needs honest assessment, not reassurance.
- **Quote the research.** When rating a section, cite what you found — "Perplexity Model Council launched Feb 5 on Max tier ($200/mo), runs queries across 3 models with synthesis" is better than "a competitor launched a similar feature."
- **Distinguish structural from cosmetic.** A competitor launching the same product is structural (invalidates positioning). A competitor changing their pricing by $5 is cosmetic (minor update to pricing section).
- **The builder decides.** Your job is assessment and execution. Never decide to abandon a plan or skip updates. Present the options and let the builder choose.
- **Check what still holds.** Not everything changes. A competitive move might invalidate messaging but strengthen a different ICP angle. Note what's reinforced, not just what's weakened.

## Common Mistakes

- **Updating docs before the checkpoint.** The builder must see the assessment first. Never modify documents in Steps 1-5.
- **Treating all impacts as equal.** "Unchanged" sections should be explicitly listed — it's reassuring to see what still holds.
- **Missing cascade effects.** A Phase 1 Competitive Landscape change almost always cascades to Phase 2 messaging and Phase 3 channel plans. Trace the full chain.
- **Overwriting originals.** Always create versioned copies. The original is the historical record.
- **Shallow research.** "The builder said X" is not enough. Research the full scope — pricing, availability, user reaction, timeline. The builder may not know the full picture.
