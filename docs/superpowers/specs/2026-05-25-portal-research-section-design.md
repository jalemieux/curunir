# Portal Research Section — Design Spec

## Overview

Add a public "Research" section to the Curunir portal: a place to publish
investment memos (PDFs with an editorial wrapper). The section serves two
purposes:

1. **Canonical permalinks** that the operator can link to from X, LinkedIn,
   email when referencing a memo.
2. **Showcase surface** for what Curunir produces — every memo is also a
   demonstration of the agent's research output.

Cadence is irregular and sparse ("published when there's something to
say"). The design must look intentional at 3 memos and still scale to
30+ without restructuring.

The look-and-feel matches the existing landing page (`portal/static/landing/index.html`):
Geist + Geist Mono, `#fafafa / #0a0a0a / #e63946` palette, triangle markers,
hard black borders, brutalist editorial grid.

## Scope

### In scope

- Public URL `/research` — the broadsheet index page.
- Public URL `/research/<slug>` — per-memo permalink with an editorial hero,
  summary block, embedded PDF, and "earlier memos" footer.
- File-system content model: a memo is a folder with a markdown file and a
  PDF. No database, no admin UI.
- Open Graph + Twitter card meta tags on each memo page so link unfurls on
  X / iMessage / LinkedIn look right.
- Nav link from the existing landing page to `/research`.

### Out of scope (v1)

- Admin UI for creating/editing memos. Authoring is `git add` a folder.
- Auto-generated OG images per memo. v1 uses a single static OG image.
- RSS / email subscription. Footer links present in the mocks but inert.
- Full-text search across memos.
- Categories as a routable filter (`/research/private`). v1 uses
  client-side tab UI that just scrolls the page; server-side filtering
  is a v2 if memo count grows.
- Migration of the existing landing-page carousel. The carousel keeps
  pointing at `/r/<file>.pdf` for now; updating it to link to
  `/research/<slug>` permalinks is a follow-up after the section ships.

## User-facing surface

### `/research` — Index page

Single page, broadsheet layout (mock: `mockups/research/option-c2-broadsheet-equal.html`):

- **Nav**: same as landing, with a `Research` pill marked active.
- **Masthead**: nameplate "The Curunir Memo" with `RESEARCH / BY THE AGENT`
  flag on the left and `LATEST / <date>` on the right. No volume or issue
  numbering — cadence is sparse.
- **Standfirst** under the masthead: "Investment memos · published when
  there's something to say".
- **Tab bar**: `ALL · EQUITIES · COMMODITIES · PRIVATE · MACRO · OPTIONS`.
  v1 behavior: client-side filter that hides non-matching memos. Tabs only
  render for categories with at least one memo.
- **Top fold**: three equal columns showing the three most recent memos,
  each with category kicker, headline, date/page byline, 2-line abstract,
  and a "READ →" link to the detail page.
- **Earlier memos table**: any memos past the top three render as a table
  (`date · title + 1-line desc · tags · pages`).
- **Footer**: matches landing.

### `/research/<slug>` — Memo permalink

Per memo (mock: `mockups/research/memo-detail.html`):

- **Compact masthead strip**: "The Curunir Memo" with a breadcrumb
  (`RESEARCH / CATEGORY / TITLE`).
- **Article hero**: 200px mono-metadata rail on the left (published date,
  category, format, sources count, author) and editorial title +
  dek + action row on the right. Actions: `Read PDF`, `Download`,
  `Share on X` (opens `intent/tweet` with prefilled text).
- **Summary block**: rendered markdown body from `memo.md`. Supports
  bullets, headings, pull-quotes (a custom `.pull` blockquote class),
  and links. This is what someone landing from X sees first — the
  "thesis in five lines" before they decide to open the PDF.
- **PDF embed**: full PDF rendered inline via `<iframe>`. Sized to 820px
  on desktop, 540px on mobile.
- **Earlier memos**: three-up row of other memos (most recent, excluding
  the current one), then a `SEE ALL →` link back to `/research`.
- **Open Graph + Twitter card meta** in the head, populated from
  frontmatter so X unfurls cleanly.

### URL shape

`/research/<slug>-<YYYY>-<MM>-<DD>` — e.g., `/research/spacex-ipo-2026-05-24`.

The date suffix is honest about cadence and disambiguates re-takes
(e.g., `gold-thesis-2026-04-12` vs `gold-thesis-2026-10-13`). The slug
is the folder name on disk; URL and folder name are identical.

## Content model

```
portal/content/memos/
  spacex-ipo-2026-05-24/
    memo.md           # frontmatter + summary body (markdown)
    memo.pdf          # the full report
  gold-decathlon-2026-05-16/
    memo.md
    memo.pdf
```

The folder name is the URL slug.

`memo.md` shape:

```yaml
---
title: "SpaceX IPO — Bull Case vs Bear Case"
date: 2026-05-24
category: "Private Markets"      # display label
category_slug: "private"         # used for tab filter
dek: "A working note on the SpaceX listing…"
pages: 18
sources: 14
pdf: memo.pdf                    # filename within the folder
---

## The thesis in five lines.

- **Launch cadence is the cash machine**, not Starlink…
- …
```

Required fields: `title`, `date`, `category`, `category_slug`, `dek`, `pdf`.
Optional: `pages`, `sources`. Body is optional but expected; if empty,
the detail page renders the hero and PDF embed with no summary block.

### Adding a new memo

1. `mkdir portal/content/memos/<slug>-YYYY-MM-DD`
2. Drop `memo.pdf` inside.
3. Write `memo.md` with frontmatter and summary.
4. Commit, deploy. The memo appears at `/research/<slug>-YYYY-MM-DD`
   and on the `/research` index on next request (no rebuild step).

## Architecture

The portal is a FastAPI app with Jinja2 templates already in use for
admin / sign-in pages. The research section follows that pattern.

### Components

**`portal/memos.py`** — new module.

A `MemoRepository` class that:

- On instantiation, scans `portal/content/memos/` for subdirectories.
- For each, parses `memo.md` frontmatter and renders the body to HTML.
- Caches the parsed list in memory. In production, the disk content
  changes only on redeploy, so reading once at startup is sufficient.
  In dev, expose a `reload()` method and call it on each request if
  `settings.debug` is set.
- Exposes `list()` (newest-first), `get(slug)`, and `categories()`
  (sorted, only those with at least one memo).

A `Memo` dataclass holds the parsed fields: `slug`, `title`, `date`,
`category`, `category_slug`, `dek`, `pages`, `sources`, `pdf_filename`,
`body_html`.

**`portal/research.py`** — new router module.

- `GET /research` → renders `research_index.html` with the memo list and
  the derived category list.
- `GET /research/{slug}` → renders `memo_detail.html` or returns 404 via
  `HTTPException`.
- `GET /research/{slug}/memo.pdf` → returns the PDF file (FileResponse).
  This keeps PDF URLs co-located with the permalink rather than serving
  them from a separate `/r/` mount.

**`portal/templates/research_index.html`** — Jinja template for the
broadsheet index. Built from `mockups/research/option-c2-broadsheet-equal.html`
with `{% for %}` loops over the memo list.

**`portal/templates/memo_detail.html`** — Jinja template for the per-memo
page. Built from `mockups/research/memo-detail.html` with frontmatter
fields and rendered body interpolated in.

**`portal/static/research/research.css`** — shared CSS for both pages.
Extracted from the mock `<style>` blocks. Keeps the templates clean.

**`portal/app.py`** — wires the new router and ensures the templates
directory and static dir are visible to Jinja / StaticFiles.

### Dependencies

Two new Python deps in `portal/pyproject.toml`:

- `python-frontmatter` — parses YAML frontmatter from `memo.md`.
- `markdown-it-py` — renders the summary body. Configured with the
  `commonmark` preset plus a small custom rule for the pull-quote
  (`> [!pull]` → `<blockquote class="pull">`).

Both are small, pure-Python, well-maintained. No JS build step.

### Why not …

- **Database-backed CMS** — overkill for "a few memos here and there",
  adds a write surface that needs auth, no value over `git add`.
- **Static site generator** — would require a build step in the deploy
  flow. FastAPI already renders templates; adding a SSG is more moving
  parts for the same output.
- **Serving PDFs from `/r/` (existing mount)** — works but leaks the
  flat filename to the URL. Co-locating PDFs under the slug folder is
  cleaner and keeps each memo self-contained on disk.

## Failure modes

- **Missing `memo.md`** → log a warning, skip that folder.
- **Missing required frontmatter field** → log a warning, skip.
- **Missing `memo.pdf`** → memo still lists and renders, but the PDF
  embed and download/read buttons are disabled (greyed). Allows a "summary
  only" memo if the operator chooses.
- **Slug collision** (two folders with the same name — impossible on a
  POSIX filesystem) → not handled; assumed impossible.
- **Unknown slug at `/research/<slug>`** → 404.
- **Malformed frontmatter YAML** → log full traceback, skip the memo,
  return whatever else parsed. One bad memo never takes down the
  whole index.

## Open Graph / sharing

Each memo page sets:

```html
<meta property="og:type" content="article">
<meta property="og:title" content="{{ memo.title }}">
<meta property="og:description" content="{{ memo.dek }}">
<meta property="og:url" content="{{ canonical_url }}">
<meta property="og:image" content="{{ og_image_url }}">
<meta property="og:site_name" content="The Curunir Memo">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:site" content="@curunir_io">  <!-- if a handle exists -->
```

v1 uses a single static OG image (`portal/static/research/og-default.png`,
1200×630, the Curunir mark on `#fafafa`). Per-memo OG images can be
generated later via a small Pillow script that draws the title onto the
default image.

`canonical_url` is derived from `settings.portal_base_url` + the request
path.

## Testing

Tests live in `portal/tests/test_research.py`.

- `test_repository_loads_memos` — fixture builds a tmp content dir with
  two folders; `MemoRepository` parses both, returns them newest-first.
- `test_repository_skips_malformed` — folder with broken YAML doesn't
  break loading; other memos still appear.
- `test_repository_handles_missing_pdf` — memo with no PDF still loads,
  is flagged so the template can disable buttons.
- `test_research_index_renders` — GET `/research` returns 200, contains
  all memo titles.
- `test_research_index_only_lists_used_categories` — tabs only render
  for categories with at least one memo.
- `test_memo_detail_renders` — GET `/research/<slug>` returns 200, body
  contains the title, dek, rendered summary HTML, and the OG meta tags.
- `test_memo_detail_404` — GET `/research/nonexistent` returns 404.
- `test_memo_pdf_serves` — GET `/research/<slug>/memo.pdf` returns the
  file with `application/pdf`.

Existing tests in `portal/tests/` use FastAPI's `TestClient`; the new
tests follow that pattern.

## Migration of existing PDFs

The starting set of PDFs already lives in `portal/static/landing/reports/`
(served at `/r/`). For each memo we want to publish under `/research`,
the workflow is:

1. Create the slug folder under `portal/content/memos/<slug>-YYYY-MM-DD/`.
2. Move (or copy) the PDF in as `memo.pdf`.
3. Author `memo.md` with frontmatter and a short summary.

The existing landing-page carousel is **not** updated in this spec — it
continues to link to `/r/<filename>.pdf` so nothing breaks. A follow-up
PR can change the carousel hrefs to `/research/<slug>` once the section
is live, which would route social traffic through the editorial page
rather than dumping into a raw PDF.

## Non-goals worth naming

- No comments / discussion / reactions.
- No personalization or recommendation (related memos are just the most
  recent three excluding the current one).
- No analytics in v1. If we want it later, add a single privacy-friendly
  pageview pixel (Plausible, Fathom) in the template head.
- No print stylesheet beyond what browsers do by default. The PDF *is*
  the print version.

## File-level plan (preview)

This spec is what's true after implementation. The implementation plan
will sequence:

1. Add deps, scaffold `memos.py` with `MemoRepository` + tests.
2. Add `research.py` router + templates + CSS, wire into `app.py`.
3. Migrate the starting 5–7 PDFs (operator-driven content task, scripted
   to convert the Downloads filenames into slug folders).
4. Add the "Research" nav link to the landing page.
5. Manual smoke test of all routes and the X link unfurl.

Detailed step-by-step lives in the implementation plan.
