---
name: gtm-plan
description: "Use when positioning is finalized and ready to build an actionable GTM execution plan. Trigger: builder has confirmed positioning doc from position-segment and wants to define what to do, through which channels, in what order, and how to measure success."
---

# GTM Plan

Takes confirmed positioning (`positioning.md`) and product context (`product-context.md`) and produces an actionable GTM plan — one plan per ICP, each with channels, specific actions, sequencing, success metrics, and autonomy levels. This is the "what and why" — detailed enough that the Execute phase can start Day 1 without asking strategic questions.

**Requires:** Confirmed `positioning.md` with readiness assessment checked. `product-context.md` for brand identity and competitive context. Web tools optional — same stack as other GTM skills, enhances channel research but skill works as pure synthesis without them.

## Tool Priority

Same stack as onboard-ingest and position-segment, all optional:

| Capability | Best | Fallback | Last resort |
|-----------|------|----------|-------------|
| Page content | `playwright` skill | `web_fetch` | — |
| Web research | `gemini-search` skill | `web-search` skill | Skip new research |
| Social listening | `xai-search` skill | — | Skip social layer |
| Reddit / forums | `reddit-research` skill | `xai-search` with reddit domain | Skip Reddit |
| LinkedIn | `linkedin-research` skill | `gemini-search` with LinkedIn query | Skip LinkedIn |

At run start, probe for available tools. If none are configured, the skill works as pure synthesis from the positioning and product context docs — channel plans are built from existing data only, with a warning that channel-specific research would strengthen the output.

## Workflow

### Step 0: Intake

Collect from the builder (ask only what's not provided):

1. **Positioning doc path** (required) — path to confirmed positioning file
2. **Product context path** (required) — path to product context file
3. **Output path** — where to write the GTM plan (default: `./gtm-plan.md`)

Read the positioning doc. Check the **Readiness Assessment** section:

- All checkboxes checked → proceed
- Any unchecked → stop. Tell the builder: "Your positioning isn't finalized yet. These items are incomplete: [list]. Run position-segment to completion before building a GTM plan."

Read the product context doc. Extract brand identity for downstream asset planning.

### Step 1: Extract Channel Universe

Scan all ICP cards in the positioning doc. For each ICP, extract the **Channels** field from the profile table — these are the channels where this ICP can be reached.

**Build the channel inventory:**

1. Deduplicate channels across all ICPs
2. For each channel, note which ICPs it serves
3. Categorize each channel:

| Type | Definition | Feasibility question |
|------|-----------|---------------------|
| **Pay-to-play** | No warmup needed — Meta ads, Google ads, TikTok ads, display, sponsorships | Budget |
| **Presence-based** | Needs existing presence or warmup — cold email, LinkedIn, Reddit, X, communities, content/SEO, podcasts | Current assets + warmup timeline |

4. **Blind-spot check:** Read `docs/channel-playbook.md` (shared reference). Cross-check the ICP channel data against the full channel list. Flag any channels that seem like a fit for an ICP but weren't identified in Phase 2. Present these to the builder in Step 2 as "channels worth considering."
5. If web tools are available, do a quick research pass on channel-specific tactics for this product category. Focus on what's working now, not generic best practices.

### Step 2: Builder Conversation — Assets & Constraints

Present the full channel inventory to the builder. This is a single conversation, not per-ICP.

**For presence-based channels, ask:**
- "What's your current state on [channel]?" (followers, domain age, sending history, community membership, posting history)
- "Have you used [channel] for this product before? What happened?"

**For pay-to-play channels, ask:**
- "Do you have budget for paid channels? Rough range is fine — we need to know if paid is on the table, not the exact number."

**For all channels, ask:**
- "Any channels you want to explicitly exclude?"
- "How much time per week can you dedicate to GTM?"

**Do not ask about every channel individually.** Group them: "For LinkedIn, X, and Reddit — what's your current presence on each?" One message, not three.

### Step 3: Assess Channel Feasibility

For each channel, rate feasibility based on builder input:

| Rating | Meaning |
|--------|---------|
| **Ready** | Builder has presence/budget. Can start Day 1. |
| **Warmup needed** | Builder has some presence but needs ramp-up. Note timeline. |
| **Needs investment** | Starting from zero. Significant effort before channel produces signal. |
| **Skip** | Builder excluded, no budget, or ICP data doesn't support it. |

Present the assessment to the builder. This is a checkpoint — the builder confirms which channels to activate before you build plans around them.

### Step 4: Build Per-ICP Plans

For each ICP in the positioning doc, build a complete plan. Read `templates/icp-plan.md` for the template.

**For each ICP:**

1. **Select channels** — from the feasibility assessment, pick channels that are Ready or Warmup for this ICP. Order by feasibility (ready first) and ICP-channel fit.

2. **Define per-channel actions** — specific enough that Execute knows exactly what to do:
   - Not "do outbound email" → "send 3-email sequence to [target criteria] at [cadence]"
   - Not "post on LinkedIn" → "publish 2 posts/week: 1 pain-point narrative, 1 product insight. Target [topics]."
   - Not "run ads" → "test 3 ad variants on [platform] targeting [criteria] with [budget]/day"

3. **Map messaging** — for each channel, specify which messaging angle from the positioning doc to use and why. Different channels may use different angles for the same ICP.

4. **Define targeting** — account and contact criteria specific enough to build a prospect list. Pull from the ICP card's profile and expand with channel-specific targeting (e.g., LinkedIn job titles, Reddit subreddits, ad platform audience criteria).

5. **Sequence channels** — dependency-based, not calendar-based:
   - Channels that need no data from others → parallel, start Day 1
   - Channels that need signal from others → gated, with explicit trigger ("start after cold email has 50+ sends and reply rate data")

6. **Set success metrics** — per channel, at two timepoints:
   - **Week 2:** early signal — is the channel producing any data? (open rates, impressions, replies, clicks)
   - **Week 8:** meaningful signal — is the channel producing results? (meetings, conversions, pipeline)
   - Include the measurement source (email platform, analytics, ad dashboard, manual tracking)

7. **Set autonomy levels** — per channel:
   - **Full approval:** builder reviews every action before it goes out (default for new channels)
   - **Approve first N:** builder reviews the first N actions, then autonomous (good for templated outbound)
   - **Fully autonomous:** system runs without approval (only after trust is established — builder must opt in)

### Step 5: Builder Validates

Present one ICP plan at a time. For each:

1. Show channel strategy table and sequencing
2. Walk through each channel plan: what, why, who, messaging, metrics
3. Show autonomy levels

After each ICP plan, ask: "Could Execute start this tomorrow? What's unclear, what's missing, what's wrong?"

**Convergence:** Update based on feedback, re-present changed sections. Should converge in 1-2 rounds per ICP. If the builder rejects a channel choice, check whether the issue is the channel itself or the action plan for that channel.

### Step 6: Finalize

Write the complete GTM plan to the output path using `templates/gtm-plan.md`. Single clean write incorporating all builder corrections.

**Exit gate — ask the builder directly:**

> "Looking at this plan: could Execute start Day 1 without asking you a strategic question? Specifically:"
> 1. "For each channel — does Execute know exactly what to do, who to target, and what to say?"
> 2. "For each channel — does Execute know what 'working' looks like at week 2 and week 8?"
> 3. "Is the sequencing clear — what runs in parallel, what's gated, and what triggers the gates?"
>
> "If yes to all three, we're done. If not, what's missing?"

## Tips

- The positioning doc already has messaging angles, proof points, and tone per ICP. The plan's job is to map these to specific channels and formats — not to reinvent messaging.
- Presence-based channel assessment is the most valuable part of the builder conversation. Don't rush it. A channel that's "ready" vs. "needs warmup" changes the entire sequencing.
- Success metrics should be realistic for a solopreneur. "100 meetings in week 2" is fantasy. "5 replies out of 50 cold emails" is a real signal.
- Autonomy levels default to full approval. The builder opts into autonomy — the system never assumes it.
- When the positioning doc has 2+ ICPs, each plan stands alone. Don't optimize across plans or worry about resource conflicts — that's Execute's problem.

## Common Mistakes

- **Recommending channels without feasibility data** — "you should be on LinkedIn" means nothing if the builder has 12 connections and no posting history. Always assess current state before recommending.
- **Calendar-based sequencing instead of dependency-based** — "start LinkedIn in week 3" is arbitrary. "Start LinkedIn after cold email reply data confirms the messaging angle" is useful. Sequence by data dependencies, not dates.
- **Generic actions instead of specific ones** — "do content marketing" is not a plan. "Publish 2 posts/week on [topics] targeting [keywords] on [platform]" is a plan. Execute should never have to interpret the action.
- **Skipping the builder conversation** — the plan can't be built from positioning data alone. Channel feasibility requires builder input. Don't generate a plan and then ask "does this look right?" — gather constraints first, then build.
- **Over-planning gated channels** — if a channel depends on data from another channel, the plan for it will be partially speculative. That's fine. Note the dependency and the trigger. Don't write a detailed 8-week plan for a channel that might not activate.
- **Setting unrealistic metrics** — solopreneurs don't have enterprise-scale data. 50 cold emails is a reasonable first batch. 5,000 is not. Scale metrics to the builder's capacity.
- **Assuming autonomy** — every channel starts at full approval unless the builder explicitly opts out. "The system will send emails autonomously" is never the default.
