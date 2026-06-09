# Smoke Test Run — {{idea_name}}

<!--
This is a smoke-test run file produced by the gtm-smoke-test skill.
Phase A (setup) filled in metadata, variants, and posting checklists.
Phase B (monitor) appends stat snapshots and analysis over time.
Do not rewrite prior sections — append-only.
-->

## Metadata

| Field | Value |
|-------|-------|
| Idea | {{idea_one_liner}} |
| Run ID | {{YYYYMMDD}}-{{idea_slug}} |
| Posted date | {{YYYY-MM-DD}} |
| Price | {{price}} |
| Location(s) | {{city}} |
| Venue(s) | Facebook Marketplace |
| Variant count | {{n}} |
| Builder | {{builder_name}} |

---

## Variants

### Variant 1 — {{axis_name}} (e.g. Functional)

**Angle summary:** one sentence describing the positioning this variant tests.

**Title:**
```
{{title — 60 chars or fewer, front-loaded hook}}
```

**Description:**
```
{{description — plain prose, humanized, 200–600 words}}
```

**Category:** {{Marketplace category}}
**Condition:** New
**Photos / photo prompts:**
1. Hero — {{prompt}}
2. Detail — {{prompt}}
3. Scale — {{prompt}}
4. (optional) In-use — {{prompt}}
5. (optional) Environment — {{prompt}}

**Posting checklist:**
- [ ] Photos uploaded
- [ ] Title pasted
- [ ] Price set
- [ ] Category picked
- [ ] Condition set to New
- [ ] Description pasted
- [ ] Location set to {{city}}
- [ ] "Hide from friends" toggled ON
- [ ] Posted
- [ ] Listing URL recorded below

**Listing URL:** `<paste after posting>`

---

### Variant 2 — {{axis_name}} (e.g. Emotional)

_(same structure as Variant 1)_

---

### Variant 3 — {{axis_name}} (e.g. Identity)

_(same structure as Variant 1)_

---

### Variant 4 — {{axis_name}} (e.g. Pain / Avoidance)

_(same structure as Variant 1)_

---

## Stats Log

Appended by Phase B on each check. Dated snapshots, newest at the bottom.

### Snapshot — {{YYYY-MM-DD HH:MM}} ({{days_live}} days live)

| Variant | Views | Saves | Messages | High-tier msgs | Medium-tier | Low-tier | Spam |
|---------|-------|-------|----------|----------------|-------------|----------|------|
| 1 — Functional | | | | | | | |
| 2 — Emotional | | | | | | | |
| 3 — Identity | | | | | | | |
| 4 — Pain | | | | | | | |

**Inbound messages (paste content or summaries):**

- Variant 1:
- Variant 2:
- Variant 3:
- Variant 4:

**Builder's gut read:** `<free-form note>`

---

## Analysis Log

Appended by Phase B on each check. Append-only.

### Analysis — {{YYYY-MM-DD HH:MM}}

- Per-variant ranking:
- Benchmark comparison (vs `references/marketplace.md` § Benchmarks):
- Message quality pattern:
- Cross-variant observations:
- Decay / growth vs previous snapshot:

---

## Verdict

_(Written by Phase B only once thresholds are met — ≥ 72h live AND ≥ 100 total views AND ≥ 2 stat snapshots. Until then, leave empty.)_

**Status:** `<pending | strong demand | moderate | weak demand | wrong audience | inconclusive>`

**Winning variant:** `<variant number + axis, or "no clear winner">`

**Evidence (top 2–3 observations):**
1.
2.
3.

**Top risks / caveats:**
-

**Recommended next step:**
- [ ] Run again at different price
- [ ] Run again in different city
- [ ] Run again with different angles
- [ ] Move to `gtm-onboard-ingest` — demand signal is sufficient to commit
- [ ] Kill — demand is not here
- [ ] Other: `<describe>`

---

## Run Context

Notes the builder wants to preserve for future reference — what inspired this test, constraints at the time, related runs, etc.

`<free-form>`
