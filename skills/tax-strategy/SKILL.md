---
name: tax-strategy
description: "Use for US federal tax questions on investments — tax implications of a sale, tax-loss harvesting, the wash-sale rule, capital gains vs. ordinary income, holding period (short- vs. long-term), cost basis, crypto/digital-asset taxation, capital-loss limits and carryover, IRA/retirement contribution limits, brackets/rates, and estimated tax. Trigger phrases: 'tax implications', 'tax-loss harvesting', 'wash sale', 'capital gains', 'holding period', 'is this short- or long-term', 'cost basis', 'crypto taxes', 'how is this taxed'."
portal_summary: "Ground US federal tax questions in current-year IRS guidance"
portal_starter: true
---

# Tax Strategy

Answer US federal tax questions by grounding every load-bearing claim in the
**current** authoritative IRS source — never from training knowledge. Tax rules
change yearly, crypto treatment is legislation-sensitive, and a stale or
hallucinated rule is worse than no answer. The whole point of this skill is to
force a fetch-and-quote of the live IRS page *before* you opine.

This is the tax-specific sharpening of the persona's no-general-knowledge
guardrail: for US federal tax, the authoritative source is narrow and known —
the **IRS** (`irs.gov`). The source map below points you at the right page so
you fetch the authority directly instead of searching and guessing.

## Scope — read this first

- **US federal tax only.** State and local tax (SALT, state capital-gains
  rules, residency) and non-US tax are **out of scope**. If the question is
  about state or foreign tax, say so plainly and stop — do not improvise.
- **Not professional advice.** You inform; you do not file, represent, or
  replace a CPA/EA. Close every answer with a one-line caveat (see below).

## IRS source map

For any tax topic, `web_fetch` the mapped page and quote the relevant passage.
URLs below were verified live on **2026-06-10**; if one 404s, fall back per the
tiered fallback rules rather than guessing a replacement.

| Topic | Authoritative IRS source | URL |
|---|---|---|
| Capital gains/losses, holding period (short vs. long-term), rates | Topic no. 409 | https://www.irs.gov/taxtopics/tc409 |
| Investment income/expenses, **wash-sale rule** (IRC §1091), capital-loss limit & carryover | Publication 550 | https://www.irs.gov/forms-pubs/about-publication-550 |
| Cost basis / adjusted basis | Publication 551 | https://www.irs.gov/forms-pubs/about-publication-551 |
| Basis of assets (quick reference) | Topic no. 703 | https://www.irs.gov/taxtopics/tc703 |
| Digital assets / crypto — current treatment hub | IRS *Digital Assets* | https://www.irs.gov/businesses/small-businesses-self-employed/digital-assets |
| Crypto — detailed Q&A | Virtual currency FAQ | https://www.irs.gov/individuals/international-taxpayers/frequently-asked-questions-on-virtual-currency-transactions |
| Crypto — foundational guidance (property treatment) | Notice 2014-21 | https://www.irs.gov/pub/irs-drop/n-14-21.pdf |
| Crypto — hard forks | Rev. Rul. 2019-24 | https://www.irs.gov/pub/irs-drop/rr-19-24.pdf |
| IRA/retirement contribution limits (current-year figures) | Retirement topics — IRA contribution limits | https://www.irs.gov/retirement-plans/plan-participant-employee/retirement-topics-ira-contribution-limits |
| Annual COLA adjustments (401(k), SIMPLE, catch-up, DB limits) | COLA increases for dollar limitations | https://www.irs.gov/retirement-plans/cola-increases-for-dollar-limitations-on-benefits-and-contributions |
| Brackets / rates / standard deduction | Publication 17 (+ live newsroom lookup, see below) | https://www.irs.gov/forms-pubs/about-publication-17 |
| Estimated tax — how to figure & pay | Form 1040-ES | https://www.irs.gov/forms-pubs/about-form-1040-es |
| Estimated tax — underpayment penalty & safe harbors | Topic no. 306 | https://www.irs.gov/taxtopics/tc306 |

**Annual figures (brackets, standard deduction, contribution limits).** The map
points at the *page*, not the *number* — so the skill stays correct across tax
years by design. Always read the current figure off the live page. For brackets
specifically, Pub 17 is the stable anchor; for the exact current-year inflation
adjustments, search the IRS newsroom for *"Tax inflation adjustments for tax
year <YYYY>"* (the annual Revenue Procedure) rather than reusing a prior-year
URL.

## Grounding rule — the teeth

A **load-bearing claim** is any statement of applicability, rate, dollar limit,
deadline, or holding-period threshold the user might act on. For every such
claim:

1. **Derive the tax year from the current date and state it.** Today is your
   reference. Say e.g. "For tax year 2026, …" so the user knows what the answer
   is pinned to. Never answer a rate/limit question without naming the year.
2. **Fetch before you opine.** `web_fetch` the mapped IRS source and **quote**
   the passage that supports the claim. No quote → no claim.
3. **Re-check contested/legislation-sensitive points live, every time.** Crypto
   treatment and the wash-sale rule's application to digital assets are the
   prime examples — these are exactly the points where training knowledge goes
   stale or where pending legislation may have changed the rule this tax year.
   Do not answer "the wash-sale rule doesn't apply to crypto" from memory;
   fetch the *Digital Assets* hub and Pub 550 and quote the current position.
4. **Tiered fallback** when the mapped page is dead or silent:
   - **Tier 1 — IRS** pubs, forms, topics, notices, revenue rulings/procedures
     (`irs.gov`). Always prefer this.
   - **Tier 2 — Treasury / statute / regs** — the IRC (e.g. §1091 for wash
     sales) or Treasury regulations (`law.cornell.edu`, `govinfo.gov`).
   - **Tier 3 — major-firm secondary guidance** (Big-Four / established tax
     publishers). **Label it secondary** and flag that it is not the primary
     authority.
   - **Else** — say "unverified — confirm with a tax professional." Do not fill
     the gap with training knowledge.
5. **Caveat.** Close with one line, e.g.: *"This is general information about US
   federal tax, not professional tax advice — confirm specifics with a CPA or
   enrolled agent before acting."*

## Worked example

User: *"I sold BTC at a loss last week and rebought it the next day — does the
wash-sale rule kill my loss?"*

1. State the year: "For tax year 2026, …"
2. Fetch the *Digital Assets* hub and Pub 550; quote what each says about the
   wash-sale rule and whether it currently reaches digital assets. (The
   wash-sale rule in IRC §1091 is written around "stock or securities" — whether
   it reaches crypto is exactly the legislation-sensitive point to verify live,
   not assert from memory.)
3. Quote the current IRS position; if the live pages don't squarely resolve it,
   drop to Tier 2 (IRC §1091 text) and say what's settled vs. unsettled this
   year.
4. Caveat line.

## Common mistakes

- **Answering a rate/limit/applicability question from memory.** That is the
  one thing this skill exists to prevent. Fetch and quote, every time.
- **Omitting the tax year.** "The limit is $7,500" is wrong without "for tax
  year 2026" — the number moves annually.
- **Asserting crypto/wash-sale treatment without a live re-check.** This is the
  highest-risk claim category; legislation moves. Re-fetch every time.
- **Reusing a prior-year newsroom or Rev. Proc. URL for brackets.** Those are
  year-stamped and 404 or mislead. Look up the current year live.
- **Wandering into state or foreign tax.** Out of scope — flag and stop.
- **Dropping the not-professional-advice caveat.** It closes every answer.
