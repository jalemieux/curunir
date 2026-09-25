---
name: gtm-smoke-test
description: "Use when a builder wants to validate demand for a product idea by running a fake-door / smoke-test listing where real buyers can attempt to transact. Generates angle-variant listings per venue, collects stats across runs, and issues a demand verdict. Trigger: builder has an idea (not a finished product) and wants real purchase-intent signal before investing in full onboard-ingest / position-segment work. Supports Facebook Marketplace and own-domain landing-page positioning tests on curunir.ai; Craigslist / eBay planned."
---

# Smoke Test

Run fake-door demand tests where real buyers can attempt to transact. Measures purchase intent, not community reaction. Use before committing to the full GTM pipeline when you have an idea but no product.

Goal: after one or more runs, the builder knows whether the idea has real buyer pull, which angle resonates, and what price point works — backed by engagement data from actual listings.

**What this is NOT:** community discovery (Reddit / Hacker News / Product Hunt posts). Those measure reaction to an idea; smoke tests measure willingness to transact. If the builder wants community sentiment, point them at `gtm-onboard-ingest` or `gtm-competitive-landscape` instead.

**Requires:** `humanizer` skill to de-AI listing copy. Marketplace needs no API keys — posting and stats collection are manual by design (avoids bot detection and Meta ToS issues). The landing-page venue needs the `github` skill (`GH_TOKEN`) to open the variant PR; deploy, ad launch, and stats stay with the builder.

## Supported Venues

| Venue | Status | Notes |
|-------|--------|-------|
| Facebook Marketplace | ✅ v1 | Physical-goods bias, local audience, visual listing, manual posting |
| Craigslist | planned | Text-heavy, older/pro-sumer audience, email contact flow |
| eBay | planned | Auction format = direct willingness-to-pay signal via bids |
| Landing page (curunir.ai) | ✅ | Positioning test: variant pages at `/v/<slug>/`, paid traffic per variant, beta sign-ups tagged per variant. You open a PR; the builder merges + runs ads. |

Adding a venue means dropping a new `references/{venue}.md` playbook and wiring it into Phase A venue selection.

## Workflow

### Step 0: Phase Detection

Ask the builder (or infer from arguments):

- **New run** — no prior run file exists, or builder wants a fresh test → Phase A
- **Check existing run** — builder has a run file and wants to log stats / get a verdict → Phase B

If unclear, list recent `smoke-test-*.md` files in the working directory and ask.

---

### Phase A — Run Setup

#### A1: Intake

Collect the minimum required inputs. Do not ask for things you can infer.

| Field | Required | Default |
|-------|----------|---------|
| Idea one-liner | yes | — |
| Target price | yes | — |
| Venue(s) | yes | Facebook Marketplace |
| Variant count | no | 4 |
| Location (city) | yes for Marketplace | ask builder |
| Base page | yes for landing page | ask builder (usually `/`) |
| Ad budget + network | yes for landing page | ask builder (Reddit Ads) |
| Photos available | no | skill will output image prompts if none |
| Output path | no | `./smoke-test-{idea-slug}-{YYYYMMDD}.md` |

**Do not ask the builder to write the listing copy.** That's the skill's job.

#### A2: Venue Selection & Playbook Load

For each selected venue, load the corresponding playbook and read it before generating any content:

| Venue | Playbook |
|-------|----------|
| Facebook Marketplace | `references/marketplace.md` |
| Landing page (curunir.ai) | `references/landing-page.md` |

Each playbook defines: listing format, title/description conventions, photo expectations, category selection, stat types the venue exposes, and benchmarks for interpretation.

#### A3: Generate Variant Angles

Read `references/variant-angles.md` for the framework. Generate `N` angle-distinct variants — each must sit on a **different positioning axis**, not just be reworded copy. Same idea, same price, same photos (or same photo prompts), different buyer mental model.

Default 4 axes:
1. **Functional** — what it does
2. **Emotional** — how it makes you feel
3. **Identity** — who you become
4. **Pain / avoidance** — what it helps you escape

If builder requests more than 4 variants, add axes from `references/variant-angles.md` § Extended Axes.

Present the angle list to the builder for a fast sanity check before drafting full copy: "Here are the 4 angles I'll test — any you want to swap before I write?"

#### A4: Draft Per-Venue Listings

For each (variant × venue) combination, draft the listing per that venue's playbook. For Marketplace:
- Title (60 chars max, front-load the hook)
- Description (per marketplace.md conventions — plain, direct, no bullets that look AI-generated)
- Price (same across variants in a run — price is a run-level knob, not a variant knob)
- Category selection
- Photo prompts (if no photos provided) — 3–5 per variant, consistent visual style

For a landing page (see `references/landing-page.md` § Building a Variant):
- A copy of the base page per variant (control included) at `portal/static/v/<slug>/index.html`
- Only title / H1 / subhead / one CTA line change; form retagged `source: '<slug>'`
- One ad block per variant (headline echoing the page, same image across variants)
- Price is optional — the CTA is a beta sign-up, not a purchase

#### A5: De-AI the Copy

**Mandatory.** Invoke the `humanizer` skill on every listing title and description. Marketplace buyers pattern-match AI-generated copy and skip past it. The humanizer removes the tells.

Do this in a single pass after all variants are drafted — don't skip it for speed.

#### A6: Write Run File + Posting Checklist

Use `templates/run-file.md` as the structure. Write the run file to the output path. Include:
- Metadata block (date, idea, price, location, venues, variant count)
- Per-variant sections (angle, final title, final description, photo prompts, category, posting checklist)
- Empty stats table (to be filled in Phase B)
- Empty analysis & verdict sections

Tell the builder: "Run file written to `{path}`. Post each variant on {venue(s)} following the checklist, then come back and invoke this skill again to log stats."

For a landing page, first open the variant PR per `references/landing-page.md` § Deploy Handoff, record its URL in the run file, and hand the builder the ad spec plus the budget reality check. The builder merges, launches ads once every variant URL loads, then returns for Phase B.

Cross-run log (optional, v1): if `smoke-test-log.md` exists in the project root, append a one-line entry — date, idea, price, venues, run file path. Create it if missing. This lets the builder compare across runs (different prices, different locations).

---

### Phase B — Monitor & Interpret

#### B1: Load Run File

Builder either points to a run file or the skill lists recent `smoke-test-*.md` files and asks. Read the whole thing — prior stats, prior analyses, current variants.

#### B2: Collect Stats

For each variant, prompt for current totals. For Marketplace:
- Views
- Saves
- Messages (count + paste the content or summaries)
- Shares (if shown)
- Days live

For a landing page: per-variant impressions, clicks, spend (ad dashboard) and sign-ups by `source` (portal admin).

Stat types come from the venue playbook (§ Stats Exposed).

**Also collect qualitative:** the builder's gut read, any patterns they've noticed (e.g. "all 4 messages on variant 2 asked the same question"). Qualitative often matters more than the counts.

#### B3: Analyze

Per-variant:
- Rank by engagement (saves + messages > views alone)
- Compare against venue benchmarks from the playbook (`references/marketplace.md` § Benchmarks)
- Flag message quality — low-signal ("is this still available?") vs high-signal (specific questions, willingness-to-pay probes)

Cross-variant:
- Which angle won, by how much
- Did the winning angle change over time (early vs. late behavior)
- Is the spread large enough to be signal, or within noise

If multiple prior stat snapshots exist (run has been checked before), compute growth rates and note decay.

#### B4: Verdict (if threshold met)

Verdict thresholds (all must hold). A venue playbook's own § Verdict Threshold overrides these — landing pages use clicks per variant, not views:
- At least 72 hours since posting, OR 100+ total views across variants, whichever first
- At least 2 stat snapshots (so growth is visible)

If threshold not met, skip verdict and tell the builder how much longer / how many more views to wait for.

If met, issue one of:
- **Strong demand** — engagement above benchmark, qualified messages, winning angle clear
- **Moderate / mixed** — engagement at benchmark, some qualified messages, angle winner unclear or close
- **Weak demand** — below benchmark, low-quality messages, no angle meaningfully beat the others
- **Wrong audience / wrong venue** — engagement pattern suggests the venue is the problem (e.g. Marketplace buyers don't want this price tier)
- **Inconclusive** — not enough data even though threshold technically met (rare)

Always include: top 2–3 observations with evidence, and a recommended next step (new run at different price, pivot angle, move to `gtm-onboard-ingest`, or kill).

#### B5: Update Run File

Append to the run file:
- A dated stats snapshot row per variant
- A dated analysis note
- The verdict block (only when issued — not on every check)

Never rewrite prior sections. Each Phase B run is append-only so the history is preserved.

Also update the cross-run log (`smoke-test-log.md`) with the verdict status if issued.

---

## Tips

- **Variants test angles, not copy.** "Cheaper than therapy" and "Save money on therapy" are the same angle (pain / avoidance). Different angles sit on different positioning axes entirely — see `references/variant-angles.md`.
- **Same photos across variants** (or same photo prompts). Differing photos confound the angle test.
- **Run humanizer on everything.** Even a lightly-humanized listing converts better than one that reads like Claude wrote it. Marketplace trust is earned through plain, slightly-rough copy.
- **Price is a run-level dimension.** To test price elasticity, do two runs at different prices with the same 4 angles. Don't vary price across variants within a single run — you lose the ability to attribute signal.
- **Qualitative beats counts.** 3 messages asking specific technical questions at $4K is stronger signal than 50 "is this still available?" at $1K. Weight message quality.
- **Marketplace is local.** Your results in Austin don't predict results in Portland. For a geo-sensitive product, plan multi-city runs.

## Common Mistakes

- **Treating variants as copy A/B.** Four reworded titles is not a test. Four angle-distinct positionings is a test. Reread `references/variant-angles.md` if variants start blurring together.
- **Skipping humanizer.** The listing reads AI-generated → trust drops → engagement drops → verdict is wrong for the wrong reason. This is the most common failure.
- **Issuing a verdict too early.** < 72 hours or < 100 views means the numbers are noise. Wait.
- **Writing low-signal replies to inbound messages.** The builder's replies to inbound are part of the test. Draft replies that qualify ("what specifically would you use it for?") rather than closing the sale on a product that doesn't exist.
- **Running the test on the wrong venue.** Marketplace buyers are hunting deals on used/commodity goods. Novel high-price appliances may get suppressed engagement not because demand is weak but because of venue-audience mismatch. If Marketplace returns a weak verdict on a premium product, consider that before killing the idea — it may be a venue problem, not a demand problem.
- **Forgetting to log stats across time.** A single stats snapshot is worse than useless — you can't see growth or decay. Always return for a second check at least 48 hours after the first.

## Extending the Skill to New Venues

To add Craigslist / eBay / landing-page / etc.:

1. Write a new `references/{venue}.md` playbook with the same sections as `references/marketplace.md`: listing format, conventions, stat types, benchmarks, posting checklist.
2. Add the venue to the Supported Venues table in this file.
3. Extend A2 venue selection to list the new venue.
4. That's it — the phase structure, variant framework, and verdict logic are all venue-agnostic.
