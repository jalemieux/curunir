# Facebook Marketplace — Playbook

Everything Marketplace-specific for the smoke test. Read this before drafting listings in Phase A or collecting stats in Phase B.

## Listing Format

| Field | Constraints | Notes |
|-------|-------------|-------|
| Title | ~80 chars max, but keep ≤60 for mobile preview | Front-load the hook. First 3–4 words matter most. |
| Price | integer, local currency | Same across all variants in a run |
| Category | pick one | Most appliance-like products fit "Home Goods" or "Electronics → Computers". Novel categories (AI hardware) have no perfect slot — pick the closest and live with it. |
| Condition | "New" or "Used - Like New" | "New" is the honest choice for a smoke test — the listing implies a product that exists. |
| Description | 200–600 words works best | Plain prose. No emoji clusters, no bullets that look like a spec sheet. Conversational first paragraph, specifics in the middle, CTA at the end. |
| Location | set to target city | Runs are city-scoped. Austin ≠ Portland ≠ Brooklyn — don't cross-compare variants across cities. |
| Delivery / pickup | "Local pickup" works; "Shipping" invites more reach but more spam | Pick local pickup for v1 — the buyer base is self-filtering, and inbound spam is lower. |

## Copy Conventions

Marketplace listings that convert tend to share these traits — matches how real humans write, not how product marketers write.

**Do:**
- Open with a short, direct sentence. Not a headline.
- Mention *why* you're selling ("cleared my desk", "extra from a batch", "decided to sell the spare") — creates seller persona even on fake-door
- Include specifics — dimensions, weight, whether it's quiet, power draw. Buyers trust listings that anticipate their questions.
- Use lower-case for casual warmth where it fits the persona — uppercase-every-word titles read like dealerships.
- One clear CTA near the end: "serious buyers message me" or "ping me if interested"

**Don't:**
- Em-dashes used as bullet separators (AI tell)
- Triple adjectives stacked ("sleek, modern, powerful") (AI tell)
- "Reach out" / "happy to" / "don't hesitate" (AI slop markers)
- Marketing-page phrasing ("unlock your potential", "take your X to the next level")
- All-caps hooks ("MUST SELL!", "PRICE REDUCED!") — reads desperate, kills trust on novel products
- Bullet lists of features as the *entire* description (reads like a spec sheet pulled from Claude)

## Photos

Three to five photos per variant. Same photos across variants within a run — differing photos confound the angle test.

If the builder has no photos, generate prompts for an image model and note which prompts go with which composition. Spec:

| Shot | Purpose |
|------|---------|
| 1. Hero | Product in context (on a desk, beside other objects for scale). No pure-white studio shots — those read "stock" and kill trust. |
| 2. Detail | Close-up on a distinctive physical feature (a port, a panel, a screen). Proves the product is real. |
| 3. Scale | With a hand, a book, or a common object for size reference. |
| 4 (optional). In-use | Plugged in, screen on, light on — shows it works. |
| 5 (optional). Environment | The room it lives in. Signals how it integrates. |

For fake-door runs where the product doesn't physically exist, image-model generation is the workflow. Spec a consistent style (lighting, background, angle) so the 3–5 shots look like they're of the same physical object.

## Pricing Notes

- Marketplace buyers are trained to haggle. Price ~5–15% above your real target to leave room.
- Round numbers ($400, $2000) feel more marketplace-native than precision ($399, $1997) for higher-ticket items.
- "OBO" (or best offer) signals willingness to negotiate. Include it if you want more messages.
- Very-low prices on novel / high-value products trigger scam suspicion. Counterintuitively, pricing *too low* can suppress engagement.

## Stats Exposed by Marketplace

Collect these per variant in Phase B:

| Stat | Where it's shown | Signal |
|------|------------------|--------|
| Views | Seller dashboard on each listing | Reach / impression count. Baseline signal. |
| Saves | Dashboard (shown as "saves" or a bookmark count) | Higher-intent signal than views. Someone's considering it. |
| Messages | Inbox, scoped per listing | Highest-intent signal — buyer took the action to reach out. |
| Shares | Sometimes shown (not always) | Rare but strong — someone thought it was worth showing a friend. |
| Listing age | Shown as "posted X ago" | Used to normalize other stats per day. |

Marketplace **does not expose** click-throughs, time-on-listing, or demographic data.

### Message Quality Tiers

Not all messages are equal. Categorize inbound per variant:

| Tier | Pattern | Count toward signal? |
|------|---------|----------------------|
| High | Specific technical questions, use-case questions, ask about availability / timing, price negotiation | Yes — strongest signal. Track these separately. |
| Medium | "Is this still available?" with follow-up after reply, or variant-specific language referencing the listing | Yes, with half-weight. |
| Low | "Is this still available?" only, no follow-up after your reply | Mostly noise. Track count but don't let it drive the verdict. |
| Spam | Payment platform scams, "send to [email]", off-platform redirects | Ignore entirely. Do not count. |

## Benchmarks

Rough heuristics for interpreting the numbers. These vary by city, category, and price — use as a first-pass sanity check, not as thresholds.

### By price tier

| Price range | 7-day views | Saves/views | Messages/views | "Strong" threshold |
|-------------|-------------|-------------|----------------|---------------------|
| < $100 | 50–300 | 2–8% | 2–10% | Messages/views > 10% |
| $100–$500 | 30–150 | 2–6% | 1–5% | Saves/views > 8% OR ≥ 3 high-tier messages |
| $500–$2K | 20–80 | 1–4% | 1–3% | Saves/views > 5% OR ≥ 2 high-tier messages |
| $2K–$5K | 10–40 | 1–3% | 0.5–2% | Saves/views > 4% OR ≥ 1 high-tier message in first 72h |
| > $5K | 5–25 | 0.5–2% | 0.5–1% | Any high-tier messages at all is meaningful |

### By venue quirk

- **Novel / unusual products** get below-benchmark views regardless of demand. Marketplace's recommendation algorithm favors categories with high existing supply. Weight saves and message quality heavier than view count when the product is novel.
- **Higher price** disproportionately reduces all engagement rates. A $5K listing at benchmark is a stronger signal than a $50 listing at benchmark.
- **Cities** vary by 3–5× in engagement for comparable listings. A run in SF / NYC / LA will see more than a run in a smaller metro. Don't compare absolute numbers across cities; compare ratios and variant deltas.

## Posting Checklist (per variant)

Hand this to the builder at the end of Phase A, per variant:

1. Open Facebook Marketplace → Sell → Item for sale
2. Upload the photos (same set across variants, order matters — hero first)
3. Paste the **title** from the run file
4. Set **price** (same across variants this run)
5. Pick **category** (from the run file recommendation)
6. Set **condition** to New
7. Paste the **description**
8. Set **location** (the city specified in the run file)
9. **Hide from friends** — toggle "Hide from friends" in listing settings to avoid polluting your social graph with test listings
10. Post
11. Note the listing URL in the run file's per-variant section
12. Move to the next variant. Space variants by a few minutes so they don't all appear as a burst to Marketplace's anti-spam system.

## Inbound Reply Templates

When messages come in during the test, the builder's replies are part of the experiment. Reply to every high or medium-tier message within 24h. Use replies that **qualify** interest without closing a sale on a product that doesn't physically exist.

**High-tier (serious question):**
> Thanks for reaching out. Happy to answer — before I do, quick question: what specifically would you use it for? I've had folks ask for different reasons and want to make sure I'm describing the right angle.

**Medium-tier (generic "still available?"):**
> Still available. One thing — I've had a lot of interest, so I'm prioritizing buyers who can tell me what they'd actually use it for. Mind sharing?

**Someone asks to buy immediately:**
> I'm finishing up a few details on this unit — can I confirm specifics with you by [day + 3] and circle back? In the meantime, is there anything specific about it you want me to confirm?

Do NOT:
- Commit to a delivery date
- Accept payment
- Redirect to an external site / form (Marketplace will flag and suppress the listing)

The goal of replies is to collect qualitative signal while keeping the seller persona believable. When the test concludes, the builder can either follow up honestly ("I'm actually researching interest before building — mind if I ask a few more questions?") or let conversations go cold.

## Known Marketplace Gotchas

- **Listings in novel categories get low algorithm reach.** If view counts are tiny after 48h, the algorithm may have buried the listing, not that demand is absent. Consider re-posting.
- **Meta may flag listings for "novel / non-standard products."** If a listing gets auto-removed, the rejection notice usually names the policy; reword to fit closer to an existing category.
- **Brand names in titles** trigger stricter review. "Gemma 4 26B AI box" may get flagged faster than "Private AI Assistant — Desktop Unit."
- **Posting the same listing in multiple cities** from one account can get the account soft-banned. Use multiple cities only if the builder has separate accounts or is willing to rotate over time.
- **Marketplace search is local-first.** Your listings mostly reach people within ~30 miles of the set location. National reach requires explicit shipping toggling, which also invites spam.
