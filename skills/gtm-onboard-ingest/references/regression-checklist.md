# Regression Checklist

Failures and friction observed during the WordSnap (wordsnap.ai) onboard-ingest run on 2026-03-22. Use this checklist on the next run to verify whether the skill enhancements fixed these issues.

## How to use

After completing an onboard-ingest run, review each item below. Check whether the issue recurred. If it did, the skill enhancement didn't work and needs further revision.

---

## Checklist

### Tool Usage

- [ ] **MCP tools were probed at run start (Step 0a)**
  - Previous failure: Skipped entirely. Defaulted to `web_fetch` for everything despite better tools being available.
  - What to check: Does the output document include a filled-in Tool Coverage table? Were available MCPs actually used?

- [ ] **xAI API (`xai-search` skill) was used for social listening**
  - Previous failure: X/Twitter social listening was completely skipped. Zero tweets searched.
  - What to check: Does the research include X/Twitter findings? If xAI API was unavailable (missing `XAI_API_KEY`), was the gap flagged to the builder?

- [ ] **Playwright browser was used for Reddit**
  - Previous failure: Reddit returned 403 on every fetch. All Reddit buyer language came from secondary aggregator sites.
  - What to check: Were Reddit threads accessed directly? If Playwright was unavailable, was it noted in the coverage gaps?

### Research Direction

- [ ] **Buyer hypothesis was checked before Layer 2 (Step 1.5)**
  - Previous failure: Full Layer 2 research targeted "knowledge workers frustrated with Grammarly." Builder corrected to "ESL speakers who don't use writing tools." Entire market research had to be redone.
  - What to check: Did the system present a buyer hypothesis after Layer 1 and wait for builder confirmation before Layer 2?

- [ ] **Buyer-segment-specific competitors were found in the first pass**
  - Previous failure: ESL-specific competitors (Ries, Engram, InstaText, Natively, CleverType) were only discovered in the second research round, after the buyer correction.
  - What to check: Were segment-specific competitors found during the initial Layer 2 research, using the buyer-segment-specific query patterns?

- [ ] **Buyer-segment-specific language was collected in the first pass**
  - Previous failure: Initial buyer language focused on native English speaker complaints about QuillBot/Grammarly. ESL-specific quotes (fear, shame, self-silencing) required a separate research round.
  - What to check: Does the buyer language section reflect the confirmed buyer segment from the start?

### Disambiguation

- [ ] **Product name collision was detected early**
  - Previous failure: "WordSnap" matched both a communication tool and an unrelated flashcard app. Multiple searches returned the wrong product. The wrong Product Hunt listing was fetched.
  - What to check: Was the namespace collision identified during Step 1 (Layer 1)? Was the builder's URL used as the primary anchor instead of the product name?

- [ ] **Product Hunt listing was found or builder was asked**
  - Previous failure: 5+ searches couldn't find the PH launch. It was buried by the flashcard app's listing. The builder had to provide the URL manually.
  - What to check: After 2 failed searches, was the builder asked for the direct URL?

### Document Quality

- [ ] **Source URLs are present on every quote and claim**
  - Previous failure: Buyer Language section used source names ("Quora", "LinkedIn", "CleverType") instead of clickable URLs. Builder caught this.
  - What to check: Does every quote in the Buyer Language section have a `[source name](URL)` format link?

- [ ] **Document was written once after convergence, not incrementally**
  - Previous failure: Document was written after Layer 2, then edited 12+ times across two rounds of builder corrections.
  - What to check: Was the document written as a single clean file after the builder confirmed findings (Step 6)? Were findings presented in conversation during Steps 3-5?

### Intake Efficiency

- [ ] **Builder was not asked unnecessary intake questions**
  - Previous failure: Not severe in this run (correctly skipped most intake items), but the skill didn't explicitly guide this.
  - What to check: When the builder provides a product name + URL, does the system proceed directly to Layer 1 without asking about social handles, competitors, or uploads?

### Coverage Gaps

- [ ] **All research gaps were flagged explicitly to the builder**
  - Previous failure: X/Twitter was silently skipped. Reddit access failure was only noted internally by the research agent, not communicated to the builder.
  - What to check: Does the synthesis (Step 3) include a "Coverage gaps" section listing what couldn't be accessed?

---

## Summary Metrics

On next run, record:

| Metric | WordSnap Run (2026-03-22) | Next Run |
|--------|--------------------------|----------|
| Research rounds needed | 2 (full redo after buyer correction) | |
| Builder correction rounds | 2 | |
| Document edits after first write | 12+ | |
| MCP tools used | 0 of 4 available | |
| X/Twitter searches performed | 0 | |
| Reddit threads accessed directly | 0 | |
| Source URLs missing from quotes | ~20 (all of Buyer Language section) | |
| Silent coverage omissions | 2 (X/Twitter, Reddit) | |
| Wrong-product searches | 3+ (flashcard app PH page) | |
