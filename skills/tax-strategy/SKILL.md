---
name: tax-strategy
description: "Use for US federal tax questions on investments — tax implications of a sale, tax-loss harvesting (incl. at household scope), the wash-sale rule (incl. across accounts), replacement securities, building a tax budget, capital gains vs. ordinary income, holding period (short- vs. long-term), cost basis, crypto/digital-asset taxation, capital-loss limits and carryover, IRA/retirement contribution limits, brackets/rates, and estimated tax. Trigger phrases: 'tax implications', 'tax-loss harvesting', 'tax-loss harvesting at household scope', 'wash sale', 'wash sale across accounts', 'replacement security', 'tax budget', 'capital gains', 'holding period', 'is this short- or long-term', 'cost basis', 'crypto taxes', 'how is this taxed'."
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

## Tax-loss harvesting mechanics

Tax-loss harvesting (TLH) sells a position at a loss to bank the capital loss
while keeping the portfolio roughly invested. Three mechanics decide whether a
harvest actually works. Each is a **load-bearing claim** — ground it per the
rule above (state the year, fetch and quote Pub 550 / IRC §1091), and present
the result as a *planning consideration*, never a directive to sell.

### Wash-sale window (household scope)

The wash-sale rule (IRC §1091, explained in Pub 550) **disallows** a loss when
"substantially identical" stock or securities are bought within a **61-day
window**: the **30 days before** through the **30 days after** the loss sale
(the sale day itself is the center, not an endpoint). A disallowed loss isn't
gone — it's added to the basis of the replacement shares — but it is deferred,
which usually defeats the point of the harvest.

The trap this skill exists to flag: the window is **not scoped to the account
where the loss was booked.** Before calling a loss "clean," check the whole
**household-controlled** set of accounts for a triggering buy in the ±30-day
window:

- **The taxpayer's own accounts**, taxable *and* tax-advantaged. A repurchase
  inside an **IRA or Roth IRA** triggers the wash sale (IRS Rev. Rul. 2008-5)
  — and there the disallowed loss is *permanently* lost, because IRA basis
  doesn't carry it forward. This is the worst-case version.
- **A spouse's accounts.** The rule reaches a buy by the taxpayer's spouse, so
  a household filing jointly must look across both partners' accounts.
- **DRIP and auto-reinvestment.** A dividend-reinvestment plan or a fund's
  automatic distribution reinvestment is a **purchase** — a small DRIP buy of
  the same security inside the window taints (at least partially) the loss. Ask
  whether DRIP is on for the security being harvested.

Whether the wash-sale rule reaches **digital assets** is legislation-sensitive
and unsettled — IRC §1091 is written around "stock or securities," and crypto
is currently treated as property. **Do not assert the answer from memory**:
re-fetch the *Digital Assets* hub and Pub 550 and quote the current position,
exactly as in the crypto worked example above.

### Replacement security

To keep market exposure while banking the loss, the standard move is to rotate
the proceeds into a **replacement security** that tracks similar exposure but is
**not "substantially identical"** to the one sold. The judgment that matters —
and the one to surface — is the *substantially-identical* line: two S&P 500
index funds from different issuers are widely viewed as risky-to-identical;
swapping one broad-market fund for a different-index fund (e.g. a total-market
fund for an S&P 500 fund) is the more defensible distance. The IRS has not
published a bright-line test, so this is a risk judgment, not a settled rule.

Your job is to **explain the substitution principle and the
substantially-identical risk** so the user (with their advisor) can choose — not
to pick tickers. **Do not recommend a specific replacement security as tax
advice**; naming exact funds is investment selection that defers to the user or
their advisor. Frame it as: "the principle is X; whether fund A and fund B are
substantially identical is the risk to weigh," and keep the not-advice caveat.

### Tax-budget table

A **tax budget** plans how much loss to harvest (or gain to realize) against the
year's running gains. Use this canonical shape so the numbers are legible and
year-stamped:

| Line | Amount | Notes |
|---|---|---|
| Short-term capital gains (YTD) | $X | taxed as ordinary income |
| Long-term capital gains (YTD) | $Y | preferential LTCG rate |
| Realized losses (YTD) | $(Z) | already booked this year |
| Capital-loss carryforward (from prior years) | $(C) | from last year's return |
| **Net position before harvest** | $(X+Y−Z−C) | gains net of losses/carryover |
| **Target harvest this year** | $H | the loss you plan to realize |

Grounding (quote Pub 550): capital losses first offset capital gains of the
same character, then the **opposite** character; any **net** capital loss then
offsets up to **$3,000 of ordinary income per year** ($1,500 if married filing
separately), and whatever remains **carries forward** indefinitely to future
years. So harvesting beyond (net gains + $3,000) doesn't save tax *this* year —
it just builds carryforward.

**Worked example** (tax year 2026; figures illustrative, confirm the $3,000
limit live in Pub 550):

| Line | Amount | Notes |
|---|---|---|
| Short-term capital gains (YTD) | $4,000 | ordinary-income rate |
| Long-term capital gains (YTD) | $10,000 | LTCG rate |
| Realized losses (YTD) | $(2,000) | already booked |
| Capital-loss carryforward | $(1,000) | from 2025 return |
| **Net position before harvest** | $11,000 | $14,000 gains − $3,000 losses/carryover |
| **Target harvest** | up to ~$14,000 | $11,000 to zero out net gains, + $3,000 to offset ordinary income |

Above ~$14,000 of additional harvested loss in this example, the excess only
adds to next year's carryforward rather than reducing 2026 tax.

## Tool boundary

The balance-sheet / `portfolio` engine has **no wash-sale detection and no TLH
math** — that is out of scope for the deterministic engine by design. What it
*does* give you, read-only, is the raw material: per-lot **cost basis** and
**acquired date** (`portfolio show` / `trade_history`) and **realized P/L**
(`realized_pnl`). Derive the wash-sale check and the tax budget *yourself* from
those reads, ground them per the rule above, and present them as **planning
considerations**. Never have the engine — or yourself — auto-execute a sell.

## Common mistakes

- **Answering a rate/limit/applicability question from memory.** That is the
  one thing this skill exists to prevent. Fetch and quote, every time.
- **Omitting the tax year.** "The limit is $7,500" is wrong without "for tax
  year 2026" — the number moves annually.
- **Asserting crypto/wash-sale treatment without a live re-check.** This is the
  highest-risk claim category; legislation moves. Re-fetch every time.
- **Reusing a prior-year newsroom or Rev. Proc. URL for brackets.** Those are
  year-stamped and 404 or mislead. Look up the current year live.
- **Scoping the wash-sale window to a single account.** The ±30-day window
  spans the whole household — the taxpayer's other accounts (incl. IRAs), a
  spouse's accounts, and DRIP auto-reinvestments. Checking only the selling
  account misses the taint.
- **Treating a replacement security as "safe" without the substantially-
  identical check.** "Rotate into a similar fund" is incomplete — name the
  substantially-identical risk and defer the actual ticker choice to the user/
  advisor; don't recommend a specific security as tax advice.
- **Emitting a tax-budget number without naming the tax year or grounding the
  $3,000 limit.** A "harvest up to $X" figure is wrong without the year and a
  quoted Pub 550 basis for the $3,000 ordinary-income offset and carryforward.
- **Wandering into state or foreign tax.** Out of scope — flag and stop.
- **Dropping the not-professional-advice caveat.** It closes every answer.
