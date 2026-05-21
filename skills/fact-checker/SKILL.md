---
name: fact-checker
description: "Use when asked to fact-check, verify, or independently audit a research report, article, summary, or set of claims — especially the output of a prior research session. Trigger phrases: 'fact-check this', 'verify these claims', 'is this accurate', 'audit this research', 'check this for errors'."
portal_summary: "Independently verify the claims in a report or article"
portal_starter: true
---

# Fact Checker

Independently verify factual claims by extracting them from the input and validating each against authoritative sources. The whole point of this skill is **isolation from the original reasoning** — a fact-checker biased by the writer's framing defeats the purpose.

**Prerequisite skills** — sub-agent must load `web-search` (claim verification depends on targeted searches and page reads).

## Usage

### When loaded by the main agent: delegate, do not check yourself

If you produced the content (e.g., you just ran `deep-research`), or the content is already in your context, you are the wrong checker. Your reasoning is anchored on the same sources, the same framing, the same blind spots. Always delegate:

```
delegate(task="""
Fact-check the content below. Load the `fact-checker` skill and follow the
"Sub-agent workflow" section exactly. Return the structured report as your
final response.

<<<CONTENT_TO_FACT_CHECK
[paste the exact text to be fact-checked here — research report, article,
or claims. Include any inline citations the author provided so you can
also verify whether the cited source actually supports the claim.]
CONTENT_TO_FACT_CHECK>>>
""")
```

When the sub-agent returns, surface its full report to the user verbatim. Do not soften, summarize away, or argue with its findings. If the sub-agent flagged something as Contradicted, say so plainly.

**Inline vs. file path.** Default to inline. For very large inputs (>50KB or PDFs), write the content to `workspace/scratch/input-<timestamp>.md` first and tell the sub-agent the path to read.

**If `delegate` returns a timeout** (`Sub-agent timed out after 300s`), do **not** retry with the same or larger task — you'll just hit the same wall. Tell the user the input was too broad to fact-check in one pass and ask them to scope it: "fact-check the pricing claims", "fact-check the market share figures", "fact-check the funding numbers". Scoped fact-checks complete; unscoped ones on long reports time out.

### Sub-agent workflow

You are the fact-checker. You have a fresh context window — use it. Do not assume the input's framing is correct. Treat every factual statement as a hypothesis until verified.

#### Budgets — non-negotiable

You operate inside a hard 300s timeout (set by `delegate`). A complete report with some ❓ verdicts is far more useful than an incomplete one with detailed verdicts. Keep moving.

- **Max 10 claims.** Pick the load-bearing ones — the assertions the conclusion rests on. Mark the rest **⏭️ Not examined** in a single line so the user knows what was skipped.
- **Max 2 searches + 1 web_fetch per claim.** If you don't have a verdict after that, mark **❓ Unverifiable** and move on. Do not keep searching for a source that may not exist.
- **Target completion in under 4 minutes.** If you sense yourself drifting on a single claim, abort, mark it ❓, and proceed. Always leave time to write the report.

#### Step 1 — Extract discrete claims

Read the content. Build a numbered list of **factual claims** — statements that can be verified or refuted against external evidence.

What counts as a claim:
- Specific numbers, percentages, dollar amounts, dates, durations
- Named entities and their attributes (founded in X, headquartered in Y, owned by Z)
- Attributions ("according to X", "X said Y") — verify both that X exists/said it and that the quote is accurate
- Causal or comparative statements with specifics ("X grew faster than Y in 2024")
- Cited statistics or studies
- Historical events and their details (when, where, who)

What does **not** count (skip these entirely — don't even add them to the list):
- Opinions, value judgments, framing language
- General characterizations without specifics ("AI is transforming software")
- Definitions of well-known terms

**Skip-fast categories — list them but mark ❓ Unverifiable immediately without searching.** These have no authoritative primary source and will burn your search budget for nothing:
- Private-company market share, revenue, or user counts (no entity publishes these for pre-IPO companies)
- "Developer consensus", "the community thinks", "widely considered" — sentiment claims with no named source
- "Most popular", "the leader", "best in class" rankings without a named ranking authority
- Predictions and forecasts (unless attributed to a named forecaster — then verify the attribution exists, not the prediction)
- Vibe-based growth language ("rapid rise", "took over the market", "exploded in popularity")
- Round figures used rhetorically ("billions of users", "thousands of companies")

Save your search budget for claims that *can* be verified: specific numbers attributed to a named source, dates of public events, official funding rounds, public-company financials, scientific results, court rulings, government data.

Aim for **at most 10 claims** per the Budgets section. Prioritize: load-bearing claims with specific numbers and named attributions. Everything beyond the cap → list as **⏭️ Not examined** in the report.

#### Step 2 — Verify each claim

For each claim, in order:

1. **Identify the right source class** using the Source Hierarchy below. A claim about a company's revenue → SEC filings or earnings release. A scientific claim → peer-reviewed paper or the institution's primary report. A historical fact → primary or established secondary sources.

2. **Search (≤2 queries).** Use the `web-search` skill (Brave) with a query targeted at finding the primary source — not an article that repeats the claim. Prefer queries like `site:sec.gov "company name" 10-K 2024` over generic `"company name" revenue 2024`. If the first query yields nothing usable, you get one more shot — then stop.

3. **Read the source (≤1 fetch).** Use `web_fetch` on the most promising URL, prompted to extract the specific evidence: `"Find the exact figure for X's revenue in 2024 and quote the surrounding sentence"`. Do not skim summaries — find the exact value.

4. **Render a verdict** using the taxonomy below. Quote the exact source excerpt that supports your verdict. If after the searches and fetch you don't have evidence, mark **❓ Unverifiable** — note what you searched in the Notes field — and move on. Do not spend another search trying to corroborate.

**Optional cross-check** — only when the first source is non-primary (Tier 3+) AND the claim is load-bearing for the report's conclusion, you may spend one additional search to look for primary corroboration. Skip this for everything else; the budget matters more than perfect triangulation.

#### Step 3 — Look for errors of omission

After per-claim verification, scan once more for:
- **Missing context** — a claim is technically true but presented in a way that misleads (e.g., "revenue grew 40%" while omitting that it had collapsed the prior year)
- **Stale data** — a figure was correct in 2022 but is now outdated; the report presents it as current
- **Cherry-picking** — the cited source includes important caveats the report drops
- **Misattribution** — a quote or stat attributed to the wrong person/study

Add these as separate findings in the report.

#### Step 4 — Emit the structured report

Return a single markdown report as your final response. Format:

```markdown
# Fact-Check Report

**Source:** [brief description of what was checked]
**Claims examined:** N (of M total — see "Not examined" if M > N)
**Summary:** X confirmed, Y contradicted, Z partially accurate, W unverifiable

## Claims

### 1. [Verbatim claim from the content]
**Verdict:** ✅ Confirmed | ❌ Contradicted | ⚠️ Partially accurate | ❓ Unverifiable
**Evidence:** [Quote from authoritative source]
**Source:** [Source name]([URL])
**Notes:** [Optional — discrepancy details, missing context, caveats. For ❓ verdicts, briefly say what you searched.]

### 2. [next claim]
...

## Not examined
[One-line per skipped claim. Use this if the input had more than 10 verifiable claims and you stayed within budget.]
- "[verbatim skipped claim]" — skipped to stay in budget; suggest user request a follow-up scoped fact-check
- ...

## Errors of Omission and Framing
[Bullet list — anything from Step 3. Omit this section if there are none.]

## Methodology Notes
[Brief — what sources you used, anything you couldn't verify and why]
```

Do not pad. Do not editorialize. The structure is the value.

## Verdict Taxonomy

| Verdict | When to use |
|---|---|
| ✅ **Confirmed** | Authoritative source matches the claim exactly. Numbers, dates, attributions all line up. |
| ❌ **Contradicted** | Authoritative source states a different value or directly refutes the claim. |
| ⚠️ **Partially accurate** | The core of the claim is correct, but a detail is wrong or missing important qualifier (e.g., correct figure but wrong year; correct attribution but quote is paraphrased inaccurately). |
| ❓ **Unverifiable** | No authoritative source found within the per-claim search budget. Distinguish from "false" — say what you searched and why you couldn't confirm. |
| ⏭️ **Not examined** | Skipped to stay within the 10-claim budget on a long input. Listed in the "Not examined" section, not in the main Claims list. |

Be willing to say ❓ Unverifiable, often. A fact-checker who confirms everything is useless. A fact-checker who runs out of time before producing a report is worse.

## Source Hierarchy

Prefer sources higher in this list. When a lower-tier source disagrees with a higher-tier source, the higher tier wins.

| Tier | Source type | Examples |
|---|---|---|
| 1 — Primary | Official filings, government data, the entity's own announcements | SEC EDGAR, congressional records, BLS, census, FDA, court filings, company 10-K/8-K, official press releases |
| 2 — Authoritative secondary | Peer-reviewed research, established news with editorial standards | Nature, NEJM, Reuters, AP, FT, WSJ, BBC, established academic publishers |
| 3 — Reference | Encyclopedic and reference works — useful as pointers, not as the final source | Wikipedia (follow citations to primary), Britannica, industry reference databases |
| 4 — Industry/analyst | Trade press, analyst reports, well-sourced specialist outlets | Stratechery, The Information, Bloomberg, sector trade press |
| Avoid | Single-source blogs, content farms, opinion pieces, social media (unless the claim is about a post itself) | — |

**Rules of thumb:**
- For company/financial claims: SEC filing or earnings release > news rewrite of the filing
- For scientific claims: the cited paper > a press release about the paper > a news article about the press release
- For historical claims: contemporaneous primary sources or established academic histories > popular summaries
- For "X said Y" claims: verify Y appears in a recording, transcript, or first-party publication, not a paraphrase

## Examples

**Fact-checking a deep-research report on AI code editors:**

1. Main agent finishes `deep-research` on AI code editors → has a written report with claims like "Cursor raised $100M in 2024 at a $2.5B valuation", "GitHub Copilot has 1.3M paid subscribers", "Sourcegraph pivoted to enterprise AI in 2023".
2. User: "fact-check this".
3. Main agent loads `fact-checker`, sees the "delegate" instruction, packages the report inline, calls `delegate`.
4. Sub-agent (fresh context) loads `fact-checker`, extracts ~12 claims, verifies each: Cursor funding → search Anysphere announcements + Crunchbase + TechCrunch coverage → confirm exact figure and date. Copilot subscribers → search Microsoft earnings call transcripts → find the actual figure and quarter.
5. Sub-agent flags one claim as ⚠️ Partially accurate ("Cursor raised $100M" — actual round was $105M, and the $2.5B valuation figure was from a different round) and one as ❓ Unverifiable (Sourcegraph subscriber count).
6. Sub-agent returns structured report. Main agent surfaces it verbatim to user.

**Fact-checking a paragraph the user just pasted:**

1. User pastes three paragraphs about a court case and asks "is this accurate?"
2. Main agent loads `fact-checker`, sees delegate instruction, calls `delegate` with the pasted text inline.
3. Sub-agent extracts claims (case name, court, date of ruling, vote count, key holding, dissenting justice), verifies each against the court's published opinion (Tier 1) and the case docket.
4. Returns the structured report.

## Tips

- **Search for the primary source by name, not the claim itself.** `site:sec.gov "Anysphere" 10-K` beats `Cursor revenue 2024`.
- **Quote, don't paraphrase, in the Evidence field.** The whole credibility of the report depends on the reader being able to see what the source actually said.
- **When two sources disagree, name the conflict.** Don't pick the one that matches the claim and call it confirmed.
- **Date your verifications.** A claim that's true today may not be true in six months — say "as of [date checked]".
- **Use `freshness=py` on Brave** when verifying time-sensitive claims to surface the most recent authoritative coverage.
- **Don't fix the writing — flag the facts.** It's not your job to suggest better phrasing. It's your job to say which claims are wrong.

## Common Mistakes

- **Main agent fact-checks itself instead of delegating.** Defeats the purpose. Always `delegate`. The fresh context window is the entire mechanism.
- **Main agent retries after a `delegate` timeout.** A timeout means the input was too broad. Retrying produces another timeout. Tell the user to scope the request (specific claim category) and stop.
- **Searching for a primary source that doesn't exist.** Private-company market share, "developer consensus", vibe-based rankings — no authoritative source exists. Mark ❓ Unverifiable on first recognition; do not burn the search budget.
- **Verifying every claim in a long report.** You have a 10-claim budget and a 300s wall. Pick the load-bearing claims, cap at 10, list the rest under "Not examined".
- **Spending all budget on one claim.** Two searches plus one fetch, then verdict (even if ❓), then next claim. A claim that takes 6 searches to almost-verify is a ❓ that took 6× too long.
- **Verifying against the same source the report cited.** If the report cites X and you confirm by re-reading X, you've checked nothing — you've confirmed the report quotes its own source. Find an *independent* source.
- **Treating Wikipedia as the final source.** It's a pointer. Follow its citations to the primary source and verify there.
- **Confirming based on snippets.** Snippets in search results often misrepresent the page. Use `web_fetch` with a targeted prompt to read the actual source.
- **Soft verdicts.** "Mostly correct" is not a verdict. Use the taxonomy: ✅ ❌ ⚠️ ❓.
- **Skipping unverifiable claims silently.** If you couldn't verify, say so explicitly with ❓ — and describe what you searched. Silence reads as confirmation.
- **Padding the report with restated context.** The user has the original. The report is *only* the verdicts.
- **Main agent softening or arguing with the sub-agent's findings.** Surface them verbatim. If the sub-agent says ❌ Contradicted, the user needs to see ❌ Contradicted.
