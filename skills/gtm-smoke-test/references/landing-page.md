# Landing Page (curunir.ai) — Playbook

Everything landing-page-specific for the smoke test. Read this before drafting variants in Phase A or collecting stats in Phase B.

This venue is a **positioning test on our own domain**: several copies of a live curunir.ai page that differ only in positioning copy, each with its own paid traffic and its own sign-up tag. It measures which angle converts cold visitors into beta sign-ups.

**The handoff.** You don't deploy or run ads. You do:

- write the variant pages
- open one PR that adds them
- write the ad spec

The builder does:

- merges the PR (which deploys it)
- launches the ads
- pastes stats back in Phase B

Everything below is shaped so those hand-offs are a checklist, not a conversation.

## How Variants Are Served

| Piece | Where | Notes |
|-------|-------|-------|
| Variant page | `portal/static/v/<slug>/index.html` in `jalemieux/curunir` | Served at `https://curunir.ai/v/<slug>/`. No portal code change per variant. |
| Sign-up tag | `source: '<slug>'` in the page's `/beta/signup` fetch | Every sign-up is stored with its `source`. This is how variants are counted. |
| Sign-up counts | Portal admin → beta sign-ups (builder has access) | Filter/count by `source`. |
| Click/spend data | Ad network dashboard (builder has access) | One ad per variant, so per-ad stats are per-variant stats. |

**Slug rules:** lowercase `a-z`, `0-9`, `-`, max 40 chars, unique across runs. Use `pos-<MMDD>-<letter>` (e.g. `pos-0925-a`). The slug is both the URL path and the `source` tag, so never reuse one — old sign-ups would be counted toward the new run.

## Building a Variant

1. **Pick the base page.** Ask the builder which live page is the control (usually `/`, which is `portal/static/finance/index.html`; `/assistant` is `portal/static/landing/index.html`; `/launch` is `portal/static/launch/index.html`). All variants in a run start from the same base.
2. **Copy it byte-for-byte** into `portal/static/v/<slug>/index.html` for every variant, **including the control**. The control gets its own slug and its own ads — don't send test traffic to `/`, where organic visitors and older sign-ups would mix into its numbers.
3. **Change only the positioning copy**:
   - `<title>`
   - hero `<h1>`
   - hero subhead paragraph
   - at most one supporting line near the CTA

   Same structure, same sections, same CTA, same visuals. Changing layout or imagery confounds the angle test, the same way differing photos do on Marketplace.
4. **Retag the form.** Change the `/beta/signup` body from its existing `source` (e.g. `source: 'finance'`) to `source: '<slug>'`. Grep the file afterwards; exactly one `source:` in the fetch body must remain, and it must be the slug.
5. **Add `<meta name="robots" content="noindex">`** in `<head>`. Variants are near-duplicates of the live page and shouldn't compete with it in search.
6. **Links:** the base pages use absolute paths (`/r/...` for reports, `/beta/signup`), so they work unchanged under `/v/<slug>/`. Don't rewrite them to relative paths.
7. **No new assets** unless all variants share them. If you add an image, it goes in `portal/static/v/<slug>/` for each variant (not a shared folder), so the PR stays inside `portal/static/v/`.

## Copy Conventions

Landing copy reads differently from a Marketplace listing, but the same humanizer rule applies: cold visitors from an ad bounce off anything that reads machine-written.

**Do:**
- Headline states the angle in ≤ 10 words. The visitor gives it ~3 seconds.
- Subhead makes the headline concrete: who it's for and what they get, in one or two sentences.
- Keep the angle **pure**. Variant B's headline shouldn't borrow variant A's privacy claim "for safety" — that's how variants blur into the same test.
- Match the ad. Each variant's ad headline should echo that variant's page headline, so a click lands on the promise it was sold.

**Don't:**
- Stacked adjectives, "unlock", "supercharge", "take X to the next level" (AI/marketing tells)
- Claims the product can't back. Every capability line on the base page was vetted. Don't add new ones in a variant.
- Competitor names in headlines without the builder's sign-off (trademark and ad-policy risk on both Reddit and X)

## Deploy Handoff — the PR

The PR is the deploy handoff: the builder reviews and merges, and merging to `main` deploys the portal.

```bash
# Clone once into the workspace (reuse it on later runs: git pull instead)
gh repo clone jalemieux/curunir curunir-site && cd curunir-site
git checkout main && git pull --ff-only
git checkout -b gtm/landing-<run-id>

# ... write portal/static/v/<slug>/index.html for each variant ...

git add portal/static/v/          # stage ONLY this directory
git status --short                # every line must be under portal/static/v/
git commit -m "feat(landing): positioning test <run-id> (<n> variants)"
git push -u origin gtm/landing-<run-id>
gh pr create --label gtm:landing \
  --title "Landing positioning test <run-id>" \
  --body "<PR body below>"
```

**Scope is enforced.** A CI check fails any `gtm:landing` PR that touches a file outside `portal/static/v/`. If it fails, fix the branch; don't ask for the check to be bypassed.

**PR body** — the builder reviews from this, so make it complete:

- Run ID + link to the run file (or paste its Variants section)
- One row per variant: slug, angle axis, final headline, final subhead
- The base page each variant was copied from
- "After merge: open each `https://curunir.ai/v/<slug>/`, submit a test email on one of them, and confirm it appears in admin with `source=<slug>`." Then remind the builder to exclude that test sign-up when counting.

Once the PR is open, record its URL in the run file and tell the builder it's ready for review. Don't write the ad spec's URLs as live until the builder confirms the merge.

## Ad Handoff — Traffic Spec

You write the spec; the builder creates the campaign. Put this in the run file, one block per variant.

**Structure (Reddit Ads; X Ads is equivalent):**
- One campaign per run, objective **Traffic / clicks**.
- **One ad group per variant**, each with an equal daily budget and identical targeting. Don't put all variants in one ad group: the network's optimizer will shift spend to whichever ad clicks best early, and the variants will end up with very different amounts of traffic.
- One ad per ad group, linking to that variant's `https://curunir.ai/v/<slug>/` (no UTM needed — the path is the attribution).
- Same ad image across variants (generate one with `gemini-image` if needed). Only the ad headline/body text changes, echoing the variant's page headline.

**Per-variant ad block:**

| Field | Value |
|-------|-------|
| Ad group name | `<run-id>-<slug>` |
| Destination URL | `https://curunir.ai/v/<slug>/` |
| Ad headline | ≤ 300 chars on Reddit; lead with the variant's angle |
| Ad body / CTA button | e.g. "Sign Up" / "Learn More" |
| Targeting | Communities + geo (identical across variants) |
| Daily budget | Total budget ÷ variants ÷ days |

**Budget reality check — say this to the builder before launch.** Rough cold-traffic numbers: CPC $0.50–$3, beta-sign-up conversion 2–10%.

- **Example:** $100 across 3 variants at ~$1.50 CPC buys ~65 clicks, about 22 per variant. At 5% conversion that's ~1 sign-up each, which is noise.
- **Budget for a real verdict:** ≥ 100 clicks per variant, roughly `variants × 100 × CPC` (~$450 for 3 variants at $1.50).
- **If they run smaller anyway:** call it directional in the run file, and read CTR as the main signal. With one ad per variant, CTR shows which *angle* pulls clicks, even when sign-ups are too few to compare.

## Stats Exposed

Collect per variant in Phase B. The builder pastes both sources:

| Stat | Source | Signal |
|------|--------|--------|
| Impressions | Ad dashboard, per ad group | Reach. Should be roughly equal across variants. If it isn't, the budget/structure is off. |
| Clicks | Ad dashboard | Traffic delivered to the page |
| CTR | Ad dashboard (clicks ÷ impressions) | How well the **angle** pulls in the feed, before the page is seen |
| Spend | Ad dashboard | For cost per sign-up |
| Sign-ups | Portal admin, count of distinct emails with `source=<slug>` | Conversions (exclude the post-merge test sign-up) |
| Sign-up messages | Portal admin (optional `message` field) | Qualitative — what people said they want |

Derived:

- **Conversion** = sign-ups ÷ clicks. Does the page deliver on the angle?
- **Cost per sign-up** = spend ÷ sign-ups.

**Gotcha:** sign-ups are idempotent per email. Someone who signs up on two variants is counted only under the first `source`. This is rare with separate ad groups, but it means totals across all sources are exact while per-variant counts slightly favor whichever variant a repeat visitor saw first.

**Not available:** page views, bounce rate, scroll depth, and time on page. There is no analytics on curunir.ai. Clicks from the ad dashboard stand in for visits. If the builder later adds analytics, extend this table.

## Benchmarks

Rough heuristics for cold paid-social traffic to a free beta sign-up. Use them as a sanity check, not as thresholds.

| Metric | Weak | Typical | Strong |
|--------|------|---------|--------|
| Reddit CTR | < 0.2% | 0.2–0.8% | > 0.8% |
| Click → sign-up | < 2% | 2–8% | > 10% |
| Cost per sign-up | > $40 | $10–40 | < $10 |

Interpreting a variant against the others:

- **High CTR, low conversion** — the angle pulls, but the page doesn't deliver on it. Keep the angle and rework the page copy in the next run.
- **Low CTR, high conversion** — the angle is a hard sell in the feed, but it resonates with the few who click. That suggests a niche segment; worth noting even if the variant "loses".
- **A "winner" needs ≥ 10 sign-ups and ≥ 1.5–2× the next variant's conversion.** A 3-vs-2 difference is a coin flip.

## Verdict Threshold

This overrides the generic threshold in SKILL.md B4 for this venue. All must hold:

- ≥ 72 hours live
- ≥ 100 clicks **per variant**
- ≥ 2 stat snapshots

If the clicks bar can't be met within the budget, issue **Inconclusive (underpowered)** and give the directional CTR read. Don't issue a demand verdict.

## Run File Blocks

For landing-page runs, use these blocks in place of the Marketplace-specific per-variant block and stats table in `templates/run-file.md`.

**Per-variant section:**

```markdown
### Variant <letter> — <axis> (slug: <slug>)

**Angle summary:** <one sentence>
**URL:** https://curunir.ai/v/<slug>/
**Base page:** <path copied from>
**Title / H1 / Subhead:** <final copy, humanized>
**Ad:** headline <...> · body <...> · ad group <run-id>-<slug>
```

**Top of the Variants section:** `**PR:** <url> · **Merged:** <date or pending>`

**Stats snapshot:**

```markdown
| Variant | Impr. | Clicks | CTR | Spend | Sign-ups | Conv. | $/sign-up |
|---------|-------|--------|-----|-------|----------|-------|-----------|
```

## Known Gotchas

- **Don't point ads at `/`.** Organic traffic and old `finance` sign-ups pollute the control. The control is a `/v/` copy like every other variant.
- **Merge before launch.** An ad pointing at an unmerged variant sends paid clicks to a 404. Confirm each URL loads before handing over the ad spec as final.
- **Ad review delays.** Reddit/X ad review can take up to 24–48h and may reject one variant but not the others. Start the 72h clock from when *all* variants are live, and note any variant that launched late.
- **Ad policy on finance claims.** Anything that reads like investment advice or return promises ("beat the market") can get rejected or the account flagged. Keep the finance angles about the tool, not about outcomes.
- **Cleanup.** After the verdict, the builder can delete the losing variant folders in a follow-up `gtm:landing` PR. Leave the winner up until its copy is promoted to the base page (a normal, reviewed change — not a `gtm:landing` PR, since it edits outside `v/`).
