# Investment-Memo Skill: Process-Driven Redesign

**Date:** 2026-05-15

**Status:** Design — approved, pending spec review

## Problem

The `investment-memo` skill produced a measurably worse report than
`deep-research` when both were given the same prompt ("top 10 gold
miners"). An independent comparison (Gemini) found the memo:

1. **Silently redefined the universe** — swapped "top 10 gold *miners*"
   for "top 10 *Western-tradable* names," dropping real top-10 producers
   (Navoi, Northern Star) without disclosure.
2. **Made a category error** — inserted Wheaton and Franco-Nevada, which
   are royalty/streaming companies, not miners, into a "miners" ranking.
3. **Missed a major asset** — omitted Kinross's $5B Great Bear project,
   a multi-billion-dollar catalyst that reshapes the company's risk
   profile.
4. **Rendered as a cheap-looking PDF** — produced via headless Chromium
   from HTML, instead of the typeset LaTeX output deep-research uses.

`deep-research`, with a looser structure, got all of this right.

## Root cause

The harm traces to a single design choice: **investment-memo prescribes
an output skeleton, not a process.** deep-research prescribes a process
(decompose → research → synthesize); its output shape emerges from the
question. investment-memo, via `references/shape-*.md`, hands the LLM a
fixed section outline and — critically — a fixed comparison table schema
(`| Rank | Ticker | Name | Market Cap | Fwd P/E | EV/EBITDA | ... |`).

That table schema actively caused every failure:

- An LLM handed a schema optimizes to fill it cleanly. Names with clean
  tickers and public multiples (Wheaton, Franco-Nevada) fit beautifully;
  state-owned (Navoi) or foreign-listed (Northern Star) producers do not.
  The schema silently selected the peer set.
- The wide 8-column table overflows LaTeX's default article margins and
  looks broken, so the agent improvised an HTML/Chromium render to make
  it fit — abandoning the consistent, typeset LaTeX path. PDF metadata
  confirms it: every deep-research PDF is `LaTeX via pandoc`; the memo
  was `Chromium / Skia/PDF`.

The fact-check pass did not catch any of this — fact-checking verifies
*stated claims*, not *omissions* or *category errors*.

## Goals

- Make investment-memo prescribe analytical *moves*, not an output
  *skeleton*. The memo body emerges from the question.
- Force rigorous, disclosed universe definition so the analyzed set
  always matches the category the user named.
- Pin PDF rendering to the same LaTeX-via-pandoc path deep-research uses.

## Non-goals

- **No changes to `deep-research`.** It works; leave it untouched.
- **No changes to `financial-analysis`.** It keeps bare pandoc.
- No new CSS / weasyprint / HTML rendering pipeline. The orphan
  `workspace/memo-style.css` stays orphaned; the skill must not use it.

## Design

All changes are to `skills/investment-memo/SKILL.md` and its `references/`
directory.

### 1. Delete the shape templates

Remove `references/shape-single-name.md`, `references/shape-sector-peer.md`,
`references/shape-catalyst.md`, and the now-empty `references/` directory.

The single-name / sector-ranking / catalyst distinction survives only as
a **one-paragraph mention of analytical lenses** inside SKILL.md — a cue
for which analytical moves to emphasize, never a template to open and
fill.

### 2. Step 1 — "Frame the question," with universe definition first

Shape detection is removed. Step 1 requires, explicitly and before any
research:

- **Define the universe and own the definition.** When the request names
  a category ("gold miners"), the analyzed/ranked set must be members of
  that category. A royalty/streaming company is not a miner; an ETF is
  not a single stock. If the analyst filters the set for practical
  reasons (tradability, ADR availability, liquidity), that is a judgment
  call that must be **surfaced**: state the filter explicitly, and still
  name the true category members excluded by it, one line each. Never
  silently substitute one name for another.
- Existing instrument-identification and verdict-mode determination are
  retained.

### 3. Step 5 — drop the body skeleton

- Keep the shared header block verbatim (Date / Prepared for / Prepared
  by / Subject / Thesis / Status). It is metadata; it never biased
  anything.
- Remove "the body follows the shape's outline."
- Replace it with an **analytical-move checklist** the body must cover,
  in whatever section structure fits the question: the setup; bull case;
  bear case; what the market is missing; load-bearing numbers; verdict
  (if verdict-mode is on); and — for a ranking — per-name coverage that
  explicitly includes **each name's major projects / pipeline /
  catalysts** (the move that would have caught the missing Great Bear
  project).
- **Comparison-table guidance, reversed.** "A ranking usually benefits
  from a comparison table. Build it *after* the names are chosen — the
  table displays your selected names, it does not select them. If a name
  lacks a metric (state-owned, foreign listing, no public multiples),
  leave the cell blank or mark it n/a; never drop a name because a cell
  would be empty. Keep the table to ~5–6 columns so it fits a typeset
  page."

### 4. Step 6 — fact-check caveat

Add one caveat: fact-checking catches contradicted claims, not omissions
or category errors. Completeness and universe-integrity are the author's
responsibility — the fact-check pass will not save you.

### 5. Step 7 — pin the render path

- Replace the delivery command with the same path deep-research uses:
  `pandoc {file}.md -o {file}.pdf` (LaTeX via pandoc).
- Explicitly forbid the HTML / Chromium / CSS render detour. If the table
  is too wide for the page, the fix is a narrower table (see §3), not a
  different renderer.

### 6. Common Mistakes — rewrite

- Drop the shape-detection / "read the template before drafting" items.
- Add: silent universe redefinition; mixing instrument categories in one
  ranking; missing a ranked name's major assets/catalysts; letting a
  table's columns gate which names are eligible; improvising an
  HTML/Chromium PDF render instead of LaTeX-via-pandoc.

## What stays unchanged in investment-memo

The delegation model, the financial decision tree (commodity / crypto /
private), the sentiment phase, the fact-check gate, and the verdict
logic. All are process guidance and none bias name selection.

## Acceptance criteria

- `references/` directory and its three files are gone.
- SKILL.md contains no fixed section skeleton and no pre-baked table
  schema; the lenses appear only as a one-paragraph description.
- Step 1 contains the universe-definition rule as written above.
- Step 7 specifies `pandoc {file}.md -o {file}.pdf` and forbids the
  HTML/Chromium route.
- `deep-research` and `financial-analysis` SKILL.md files are byte-for-byte
  unchanged.
