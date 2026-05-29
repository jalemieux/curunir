---
name: medical-research
portal_summary: "Look up drugs, symptoms, lab results, and clinical trials from authoritative NIH/FDA sources"
portal_starter: true
description: "Use for any medical, health, or wellness question — drugs, supplements, vitamins, vaccines, symptoms, conditions, diagnoses, lab/blood-test results, prognosis, treatments, procedures, clinical trials, adverse events, recalls, drug interactions, pill identification. Trigger phrases: 'look up drug X', 'is X safe' (during pregnancy / with Y / to take), 'has X been recalled', 'side effects of X', 'side effects of the X vaccine', 'are there clinical trials for X', 'what could be causing X', 'symptoms X Y Z what is this', 'differential diagnosis', 'could this be X', 'what do these lab results mean', 'interpret my blood work', 'is this TSH level normal', 'my CBC shows X', 'prognosis for X', 'what is condition X', 'how does X treatment work', 'what to expect from procedure X', 'should I be worried about X', 'drug interaction', 'pill identifier', 'what does the package insert say for X'."
---

# Medical Research — Authoritative API-Backed Health Data

**Disclaimer: This skill retrieves data from authoritative US government sources (NIH, FDA, NLM) for informational purposes only. It is NOT medical advice, diagnosis, or a substitute for consultation with a qualified healthcare professional. Always discuss findings with your doctor before making any health decisions.**

## Source Stack (Tier 1 — All Free, No API Key Required)

| Source | API Base | What It Provides |
|--------|----------|------------------|
| **PubMed E-utilities** | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/` | 40M+ biomedical citations — literature search, systematic reviews, meta-analyses |
| **MedlinePlus** | `https://medlineplus.gov/connect/service.html` + Health Topics WS | Patient-friendly condition summaries (1,000+ topics), drugs & supplements |
| **openFDA** | `https://api.fda.gov/` | Drug labels, adverse events (FAERS), recalls/enforcement |
| **DailyMed** | `https://dailymed.nlm.nih.gov/dailymed/services/v2/spls` | Full package inserts (SPL), pill images |
| **RxNorm** | `https://rxnav.nlm.nih.gov/REST/` | Drug name normalization, brand↔generic, ingredient lookup |
| **ClinicalTrials.gov v2** | `https://clinicaltrials.gov/api/v2/` | Registered clinical studies — conditions, interventions, phases, results |
| **HPO** | `https://clinicaltables.nlm.nih.gov/api/hpo/v3/search` | Symptom → disease mapping via standardized phenotype ontology (18,000+ terms) |

## Workflow by Use Case

### 1. Medication Research (drug name → full profile)

**Goal:** The user asks about a specific drug — safety, side effects, dosing, recalls.

**Steps:**

1. **Normalize the drug name** via RxNorm
   ```bash
   curl -s "https://rxnav.nlm.nih.gov/REST/drugs.json?name=DRUG_NAME" | jq .
   ```
   Extract RxCUI, ingredient(s), brand names, synonym list. Use RxCUI for subsequent queries.

2. **Get drug label** via openFDA
   ```bash
   curl -s "https://api.fda.gov/drug/label.json?search=openfda.generic_name:INGREDIENT&limit=5" | jq .
   ```
   Extract: indications, warnings, contraindications, dosage_and_administration, adverse_reactions, drug_interactions, use_in_specific_populations (pregnancy, pediatric, geriatric).

3. **Check adverse events** via openFDA FAERS
   ```bash
   curl -s "https://api.fda.gov/drug/event.json?search=patient.drug.medicinalproduct:DRUG_NAME&limit=10" | jq '.results[] | {reaction: .patient.reaction[].reactionmeddrapt, seriousness: .serious, sex: .patient.patientsex, age: .patient.patientonsetage}'
   ```
   Summarize top reported reactions with seriousness breakdown.

4. **Check recalls** via openFDA enforcement
   ```bash
   curl -s "https://api.fda.gov/drug/enforcement.json?search=product_description:DRUG_NAME&limit=5" | jq .
   ```

5. **Get package insert and pill images** via DailyMed
   ```bash
   # Search by drug name (DailyMed paginates with pagesize/page — not limit)
   curl -s "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json?drug_name=DRUG_NAME&pagesize=3" | jq .
   # Then fetch full SPL for a specific Set ID
   curl -s "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/SET_ID.xml"
   ```

6. **Synthesize** into a structured drug profile:
   - Drug name (brand + generic), RxCUI
   - What it's used for (indications)
   - Key warnings and contraindications
   - Most common side effects (from label + FAERS)
   - Safety in pregnancy/pediatric/geriatric (if available)
   - Drug interactions (from label — note: no structured DDI API available)
   - Active recalls (if any)
   - Link to full package insert

### 2. Adverse Event / Side Effect Investigation

**Goal:** The user experienced or heard about a side effect and wants to know if it's reported.

**Steps:**

1. **Query FAERS** — search by drug name + reaction:
   ```bash
   curl -s "https://api.fda.gov/drug/event.json?search=patient.drug.medicinalproduct:DRUG_NAME+AND+patient.reaction.reactionmeddrapt:REACTION_TERM&limit=20" | jq .
   ```

2. **Get reaction frequency** — count reports:
   ```bash
   curl -s "https://api.fda.gov/drug/event.json?search=patient.drug.medicinalproduct:DRUG_NAME&count=patient.reaction.reactionmeddrapt.exact" | jq .
   ```
   Returns ranked list of all reported reactions with counts.

3. **Cross-reference with drug label** — is it a known labeled reaction?

4. **Synthesize:**
   - Number of FAERS reports for this drug + reaction
   - Whether it appears on the official drug label
   - Relative frequency compared to other reported reactions
   - Caveats: FAERS is self-reported, does not establish causation

### 3. Symptom → Condition Mapping

**Goal:** The user describes symptoms and wants to explore what conditions they map to.

**Steps:**

1. **Map symptoms to HPO terms:**
   ```bash
   curl -s "https://clinicaltables.nlm.nih.gov/api/hpo/v3/search?terms=SYMPTOM&df=name,synonym&maxList=20" | jq .
   ```
   Search with multiple symptom terms. Note the HPO IDs (e.g., HP:0001250 — Seizure).

2. **For each matched HPO term, note:**
   - The standardized term name
   - Definition and synonyms (to confirm we're mapping the right concept)
   - Parent terms (broader categories — can suggest related symptoms the user didn't mention)

3. **Search PubMed for conditions associated with the symptom pattern:**
   ```bash
   # Use E-utilities esearch to find relevant papers
   curl -s "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=SYMPTOM1+AND+SYMPTOM2+AND+differential+diagnosis&retmax=10&retmode=json" | jq .
   # Then fetch abstracts
   curl -s "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=ID1,ID2,ID3&rettype=abstract&retmode=text"
   ```

4. **Search MedlinePlus Health Topics:**
   Use `web_fetch` on `https://medlineplus.gov/healthtopics.html` or the Health Topics Web Service to find patient-facing condition pages matching the symptoms.

5. **Synthesize** using the health-differential structure (Most Likely / Consider / Rule Out) but grounded in HPO terms and PubMed evidence.

### 4. Prognosis Research

**Goal:** The user wants to know outcomes for a specific condition or procedure.

**Steps:**

1. **Search PubMed for systematic reviews and meta-analyses:**
   ```bash
   curl -s "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=CONDITION+AND+(prognosis+OR+mortality+OR+survival+OR+outcomes)+AND+(meta-analysis+OR+systematic+review)&retmax=10&sort=relevance&retmode=json" | jq .
   ```

2. **Fetch abstracts** — extract:
   - Study design (RCT, cohort, meta-analysis)
   - Population size
   - Key outcome numbers (mortality rates, recovery rates, complication rates)
   - Follow-up period
   - Confidence intervals where reported

3. **Check ClinicalTrials.gov for active/completed trials:**
   ```bash
   curl -s "https://clinicaltrials.gov/api/v2/studies?query.cond=CONDITION&pageSize=10&filter.overallStatus=COMPLETED,RECRUITING&format=json" | jq .
   ```

4. **Synthesize:**
   - Quantified outcome ranges (never say "low risk" — say "X–Y% in published studies")
   - Factors that improve or worsen prognosis
   - Gaps in evidence (what we don't know)
   - Active research directions

### 5. Clinical Trials Lookup

**Goal:** The user wants to find trials for a condition or treatment.

**Steps:**

1. **Search ClinicalTrials.gov v2:**
   ```bash
   # By condition
   curl -s "https://clinicaltrials.gov/api/v2/studies?query.cond=CONDITION&pageSize=20&format=json" | jq .
   # By drug/intervention
   curl -s "https://clinicaltrials.gov/api/v2/studies?query.intr=INTERVENTION&pageSize=20&format=json" | jq .
   ```

2. **Extract for each trial:**
   - NCT number and title
   - Status (recruiting, active not recruiting, completed)
   - Phase (I, II, III, IV)
   - Enrollment count
   - Brief summary
   - Inclusion/exclusion criteria (for "could I qualify?" questions)
   - Locations (if recruiting)

3. **Present** as a table sorted by relevance (recruiting first, then by phase).

### 6. Drug Name Resolution & Comparison

**Goal:** "Is X the same as Y?" or "What's the generic for X?"

**Steps:**

1. **Look up both drugs in RxNorm:**
   ```bash
   curl -s "https://rxnav.nlm.nih.gov/REST/drugs.json?name=DRUG_NAME" | jq .
   ```

2. **Compare RxCUIs and ingredient lists** — same RxCUI = same drug.

3. **Check DailyMed for images** to visually confirm pill identity.

4. **Synthesize** with a clear "same drug / different formulation / different drug class" determination.

### 7. Lab Result Interpretation

**Goal:** The user shares lab values and wants to understand what they mean.

**Steps:**

1. **Parse the input** — the user may provide results as text, PDF, photo, or from memory. Extract:
   - Test name(s), value(s), reference range(s), units, lab name (if mentioned)
   - If values are ambiguous or missing key fields (no units, no range), ask for clarification

2. **Classify each result:**

   | Status | Meaning |
   |---|---|
   | **Normal** | Within standard reference range |
   | **Borderline** | Near edge — may or may not be clinically significant |
   | **High / Low** | Outside range — significance depends on context |
   | **Critically abnormal** | Dangerous territory (e.g., K⁺ >6.0, glucose >500, platelets <20) |

3. **Research clinical significance** via PubMed:
   ```bash
   curl -s "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=ELEVATED_LAB_MARKER+AND+(causes+OR+clinical+significance)+AND+review&retmax=5&sort=relevance&retmode=json" | jq .
   ```
   Research: what the test measures, common causes of abnormal values (ranked), what patterns matter (e.g., elevated ALT + AST + GGT → liver pattern), red-flag thresholds, how medications/fasting/time-of-day affect results.

4. **Structure the interpretation:**

   #### Results Summary
   | Test | Value | Reference Range | Status |

   #### Notable Findings
   For each abnormal/borderline result: what it means, common causes, pattern connections.

   #### Patterns & Connections
   Are multiple abnormal values related? Does the picture suggest a category of issue?

   #### Questions for Your Doctor
   3–5 specific, informed questions (e.g., "My TSH is slightly elevated — should we retest in 3 months or investigate further?").

5. **Caveats:**
   - Reference ranges are population-based — "normal" for one person may differ
   - A single abnormal result rarely means disease — trends matter more
   - If critically abnormal and the user hasn't been told to seek care, flag it prominently

## General Patterns

### PubMed Literature Search (Reusable)
```bash
# Step 1: Search for IDs
curl -s "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=QUERY&retmax=10&sort=relevance&retmode=json"

# Step 2: Fetch abstracts by ID
curl -s "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=ID_LIST&rettype=abstract&retmode=text"
```

**Best practices for PubMed queries:**
- Add `AND (meta-analysis[pt] OR systematic review[pt])` for highest evidence
- Add `AND "last 5 years"[dp]` for recency filter
- Use `sort=relevance` for most pertinent results first
- Always prefer systematic reviews > RCTs > cohort studies > case reports

### Rate Limits
- **PubMed:** Max 3 requests/second without an API key. Use `&api_key=` if needed (free NCBI account).
- **openFDA:** No key required. Without a key: 240 requests/minute and 1,000 requests/day per IP. With a free API key: 240 requests/minute and 120,000 requests/day. Pass the key as `&api_key=`.
- **RxNorm:** No documented rate limit, but be respectful.
- **ClinicalTrials.gov:** No documented rate limit.
- **HPO:** No documented rate limit.
- **DailyMed:** No documented rate limit.

**Between API calls, add a 1-second sleep:** `sleep 1`

## Anti-Patterns & Guardrails

### Risk Calibration (Critical)
When discussing prognosis or risk for the user or their family members:
- **Never** use qualitative reassurance language ("low risk", "should be fine")
- **Always** provide quantified ranges from published literature (e.g., "3.5–11.6% 30-day mortality in published systematic reviews")
- **Never** over-generalize positive outcomes
- If risk ranges are not available in the literature, say so explicitly

### Scope Boundaries
- This skill retrieves and synthesizes publicly available data — it does not diagnose, prescribe, or recommend treatment plans
- FAERS data is self-reported and does not establish causation — always note this
- Drug labels may not reflect the most recent FDA safety communications — supplement with FDA.gov search
- PubMed abstracts may not tell the full story — note when full text would be needed

### Drug Interaction Caveat
The NLM RxNav Drug-Drug Interaction API was discontinued January 2024. There is no free replacement. Options:
1. **Approximate via FAERS:** Query adverse events where both drugs appear in the same report — suggests possible interaction but is noisy
2. **Extract from drug labels:** openFDA drug labels include a `drug_interactions` section with text descriptions
3. **Recommend clinical consultation** for interaction questions — this is a genuine limitation

### Evidence Hierarchy
When presenting evidence, always classify:
| Level | Source |
|-------|--------|
| **Strong** | Systematic reviews, meta-analyses, large RCTs |
| **Moderate** | Small RCTs, well-designed cohort studies |
| **Limited** | Case-control studies, case series, expert opinion |
| **Preliminary** | Preclinical, in-vitro, animal studies, early-phase trials |

## Output Style

- Use tables for drug profiles, trial listings, and adverse event rankings
- Quantify everything possible — avoid vague language
- Cite every data point with a numbered inline marker resolving to the `## Sources` list (see Inline citations under Delivery) — including inside tables, so the user can verify any value with one click
- Flag confidence level of evidence explicitly
- Always close with: what to discuss with the doctor, and specific questions to ask

## Delivery

The deliverable is a written report, not just a chat reply. Don't wait to be asked, and don't ask which format to use.

1. Write the full report as markdown to `context/workspace/generated/{topic-slug}-{YYYY-MM-DD}.md` (e.g. `metformin-safety-2026-05-18.md`, `cbc-results-2026-05-18.md`). The H1 inside the document should be a descriptive title, not the filename slug. Include the disclaimer from the top of this skill. End the document with a `## Sources` section (see Inline citations below).
2. Convert it to PDF with pandoc — already installed in the environment:

   ```bash
   pandoc context/workspace/generated/{topic-slug}-{YYYY-MM-DD}.md -o context/workspace/generated/{topic-slug}-{YYYY-MM-DD}.pdf
   ```

   Never `pip install` a PDF library — pandoc is the tool. Do not render via HTML, headless Chromium, or CSS.
3. Attach the **PDF**: `attach(path="context/workspace/generated/{topic-slug}-{YYYY-MM-DD}.pdf")`. If pandoc fails, attach the `.md` as a fallback.
4. Reply with a concise summary (key findings + the doctor questions) as your text response. The full report is the attachment.

**Header block.** Open the document with Date / Prepared for / Subject lines, each separated by a blank line — markdown collapses consecutive lines into one paragraph, and pandoc will render them as a run-on sentence otherwise.

**Inline citations.** Every data point — drug-label fact, FAERS count, trial detail, lab reference range, PubMed finding — carries a clickable numbered marker that jumps to the matching entry in a `## Sources` section at the end of the document, like a research paper, so the reader can verify any claim with one click. Two pieces of plain markdown that survive `pandoc … -o … .pdf` with no extra flags or packages:

- **In the body** (including inside table cells), place the marker immediately after the claim, no space before it:

  ```markdown
  Metformin's labeled boxed warning is for lactic acidosis.[^1^](#src-1)
  ```

  The link `[…](#src-1)` points at the anchor `src-1`; wrapping the number in `^…^` renders the visible `1` as a superscript. Writing it link-first (`[^1^]` before `(#src-1)`) keeps it clear of pandoc's `^[…]` inline-footnote syntax.

- **In the Sources list**, each numbered entry begins with an empty anchor span `[]{#src-N}` matching its number:

  ```markdown
  ## Sources

  1. []{#src-1}DailyMed — Metformin SPL package insert. <https://dailymed.nlm.nih.gov/...>
  2. []{#src-2}openFDA FAERS — metformin adverse event report counts. <https://api.fda.gov/...>
  ```

Number sources in first-appearance order; reuse the same number (and anchor) when a source is cited again; for several sources on one claim, repeat the marker — `…claim.[^1^](#src-1) [^3^](#src-3)`. Every marker number must have exactly one matching `#src-N` anchor, and vice versa.

## Anti-Patterns (Delivery)

- **Pip-installing a PDF library** — pandoc is already in the environment. Use it. Never install packages at runtime to produce the attachment.
- **Attaching `.md` instead of `.pdf`** — always convert to PDF first; only fall back to `.md` if pandoc fails.
- **Forgetting `attach()`** — the report file must be attached, not just written to disk.
- **Filename slug as the H1** — the slug is for the file; the H1 should be a descriptive long-form title.
- **Bare URLs instead of numbered markers** — data points cite sources with `[^N^](#src-N)` markers resolving to the `## Sources` list, not raw inline URLs scattered through the prose and tables.
- **Marker/anchor mismatch** — every `[^N^](#src-N)` marker needs exactly one matching `[]{#src-N}` anchor in Sources, and every Sources entry must be cited. A marker with no anchor renders as a dead link in the PDF.
