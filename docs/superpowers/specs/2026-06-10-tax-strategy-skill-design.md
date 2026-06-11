# Tax Strategy Skill — Design

**Date:** 2026-06-10
**Issue:** #347
**Status:** Implemented (PR #348)

## Problem

The finance persona advertises **"tax strategy"** as a capability
(`personas/finance/persona.yaml` description), but no skill backed it. Tax
questions therefore fell through to the model's training knowledge — the worst
possible source for tax, where rules change yearly, crypto treatment is
legislation-sensitive, and a stale or hallucinated rule is more dangerous than
no answer.

This is a source-specific sharpening of the persona's existing
no-general-knowledge guardrail. For US federal tax the authoritative source is
narrow and known — the **IRS**. So the skill is a curated IRS source map plus a
grounding rule that forces `web_fetch` + quote of the current-tax-year rule
before the agent opines.

## Approach

A **prose-only** skill — no Python, no new tools, no engine integration. It
rides entirely on the existing default toolset (`web_fetch`, `read`). Two parts:

1. **IRS source map** — an inline table (topic → canonical IRS Pub/Topic/Notice
   → `irs.gov` URL) so the agent fetches the *right* authoritative page directly
   instead of searching and guessing.
2. **Grounding rule** (the teeth) — fetch-and-quote before any load-bearing
   claim, derive the tax year from the current date and state it, re-check
   contested points (crypto, wash-sale) live every time, tiered fallback,
   US-federal-only scope, and a not-professional-advice caveat.

### Why the map points at pages, not numbers

Annual figures (brackets, contribution limits, standard deduction) live on the
mapped pages. The map deliberately points at the *page*, not the *number*, and
the grounding rule forces a live fetch — so the skill stays correct across tax
years without edits. The one brittle case, year-stamped inflation-adjustment
newsroom/Rev. Proc. URLs, is handled by instructing a live newsroom lookup for
*"Tax inflation adjustments for tax year <YYYY>"* rather than hardcoding a URL.

## Source map — verification

Every `irs.gov` URL in the skill was `web_fetch`-verified live on **2026-06-10**
(200, page title matched the intended topic); URLs were transcribed from the
fetched pages, not from memory.

| Topic | Source | Verified |
|---|---|---|
| Capital gains/losses, holding period | Topic 409 | ✓ |
| Investment income, wash-sale, loss carryover | Pub 550 | ✓ |
| Cost / adjusted basis | Pub 551 | ✓ |
| Basis quick reference | Topic 703 | ✓ |
| Digital assets / crypto hub | IRS *Digital Assets* | ✓ |
| Crypto Q&A | Virtual currency FAQ | ✓ |
| Crypto property treatment | Notice 2014-21 (PDF) | ✓ (200) |
| Crypto hard forks | Rev. Rul. 2019-24 (PDF) | ✓ (200) |
| IRA contribution limits | Retirement topics — IRA limits | ✓ |
| Annual COLA limits | COLA increases | ✓ |
| Brackets / rates | Pub 17 | ✓ |
| Estimated tax — figure & pay | Form 1040-ES | ✓ |
| Estimated tax — underpayment penalty | Topic 306 | ✓ |

The year-specific *"Tax inflation adjustments for tax year 2026"* newsroom URL
404'd at verification time — confirming the brittleness the page-not-number
design avoids. Brackets therefore anchor on Pub 17 (stable) plus a live newsroom
lookup, not a hardcoded annual URL.

## Integration

- `personas/finance/persona.yaml` — `tax-strategy` added to the absolute skill
  allowlist (near the analysis/memo cluster).
- `personas/finance/README.md` — listed in the curated-skills line.
- Auto-discovered by the manifest builder; no `CLAUDE.md` change needed.

## Non-goals (YAGNI)

- No Python, no new tools, no portfolio-engine integration.
- No automated IRS link-checker (irs.gov reorganizes; a dead link degrades to
  the tiered fallback, and re-verification is a periodic manual concern).
- No graded eval task — consistent with the issue's non-goals. A graded
  grounding check against `eval/finance/` is a possible follow-up.

## Risks

- **IRS URL drift.** irs.gov reorganizes pages over time; the tiered fallback
  absorbs a dead link, but periodic re-verification is implied.
- **Over/under-triggering.** The `description` is scoped to tax-rule phrasing to
  avoid stealing turns from `balance-sheet` / `financial-analysis`.
