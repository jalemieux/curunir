# Finance Persona — Balance-Sheet Capability (A / B / D)

**Date:** 2026-06-04
**Status:** Design — awaiting review
**Related:** `2026-06-04-finance-eval-c-design.md` (the eval suite that judges this)

## Why

Finance-persona sessions on a local model failed in three buckets (diagnosed
from the 2026-06-04 archives + live `portfolios.md`):

1. **No asset schema** — free-form `portfolios.md` (+ a second `assets.md` +
   index copies); net worth that doesn't reconcile (stated $5,129,953 vs
   components summing to $5,245,421; two liability totals), gold double-counted,
   collectibles drifting, missing cost basis / acquisition dates.
2. **No position-tracking capability** — `financial-analysis` /
   `investment-memo` analyze the *market*; nothing tracks the owner's *balance
   sheet*. "Audit my net worth" had no workflow and no deterministic math.
3. **Hallucination** — the SpaceX-IPO session fabricated an S-1, a $135 price,
   a $1.75T valuation from search snippets.

This spec covers **A** (schema), **B** (the tracking capability), **D**
(prompt/guardrail hardening). **C** (evals) is a separate, in-flight spec and
is the *judge* of one decision here (the interface experiment).

## Through-line

Every choice serves one principle: **take both the arithmetic and the
fragile writes away from the local model.** A bad number is recoverable; a
confused free-form edit corrupts state. So the model neither hand-sums a
portfolio nor hand-edits the store.

## Decisions (locked through brainstorming)

- **Store: SQLite** at `context/memory/portfolio.db`. The one genuinely
  relational, numeric corner of memory; prose facts stay markdown.
- **A single Python engine** over the DB is the source of truth for every
  operation. The *surface* (how the LLM reaches the engine) is a thin adapter
  over it.
- **Dual surface, eval-judged.** Build two thin adapters — a **CLI** (skill-
  referenced, yfin.py pattern) and an **opt-in tool** (structured tool-call,
  unlocked by the skill, `to_audio` pattern) — and let **C** pick the winner
  on the local model by pass-rate + tool-call efficiency. **Not** an always-on
  core tool (it would bloat the default toolset and add routing noise, against
  curunir's small-default design).
- **Boundary-only evals.** No grader inspects the DB; correctness is judged on
  the agent's answers. The schema is free to evolve as long as answers stay
  right.

## A — the schema (SQLite)

Two tables (wide, nullable per-class columns + a JSON overflow), plus views
for the canned rollups.

```sql
CREATE TABLE assets (
  id          TEXT PRIMARY KEY,           -- stable slug, engine-assigned
  class       TEXT NOT NULL,              -- equity|real_estate|collectible|physical|cash|private|retirement
  label       TEXT NOT NULL,
  ticker      TEXT, qty REAL, avg_cost REAL,
  cost_basis  REAL,                       -- nullable; engine WARNS when absent
  value       REAL NOT NULL,              -- current value (frozen unless refreshed)
  value_asof  TEXT,
  acquired    TEXT,                       -- nullable; engine WARNS when absent
  account     TEXT,
  extra       JSON,                       -- class-specific: address, qty_oz, fund nav, …
  UNIQUE(class, label)                    -- hard stop on exact duplicates
);

CREATE TABLE liabilities (
  id           TEXT PRIMARY KEY,
  class        TEXT NOT NULL,             -- mortgage|loc|loan
  label        TEXT NOT NULL,
  balance      REAL NOT NULL, apr REAL,
  linked_asset TEXT REFERENCES assets(id) -- e.g. mortgage → property
);

CREATE VIEW v_networth AS
  SELECT a.assets, l.liabilities, a.assets - l.liabilities AS net_worth
  FROM (SELECT COALESCE(SUM(value),0)   AS assets      FROM assets) a,
       (SELECT COALESCE(SUM(balance),0) AS liabilities FROM liabilities) l;

CREATE VIEW v_rollup_by_class AS
  SELECT class, COALESCE(SUM(value),0) AS value, COUNT(*) AS n
  FROM assets GROUP BY class;

CREATE VIEW v_collectibles_pnl AS
  SELECT label, cost_basis, value, value - cost_basis AS unrealized, acquired
  FROM assets WHERE class = 'collectible';
```

- **Net worth** = `v_networth` — never hand-summed.
- **Real-estate equity** = property `value` − linked mortgage `balance`
  (engine resolves the link; optionally a `v_re_equity` view).
- **`cost_basis` + `acquired` on every asset** is what makes holding-period
  and the 28% collectibles rate computable — the exact fields that were
  missing.
- **`UNIQUE(class, label)`** kills exact-duplicate drift; near-duplicates
  (a second "blue solidbody" variant) are caught by an engine-side fuzzy warning on
  `add`.
- A **ledger** (lots / transactions tables for realized-gain tracking) is a
  natural future extension; out of scope for v1 (YAGNI).

## The engine

A single importable module — pure functions, JSON-serializable returns, a `db`
path argument (so the same code serves live memory *and* the C fixture).
Mirrors `yfin.py` conventions (importable for tests; errors as
`{"error","hint"}`).

| Reads | Writes |
|---|---|
| `networth(db)` → `{assets, liabilities, net_worth}` | `add_asset(db, **fields)` → `{id, warnings[]}` — validates required (`class,label,value`); **warns** on missing `cost_basis`/`acquired` and on a near-duplicate label |
| `rollup(db)` → by class + real-estate equity + total | `update_asset(db, id, **fields)` |
| `list_assets(db, cls=None, account=None)`, `show(db, id)` | `remove_asset(db, id)` |
| `pnl(db, cls='collectible')` → cost basis, unrealized, holding period | `import_rows(db, rows, account)` → bulk insert + validate + **account-total self-check** |
| `re_equity(db, property_id)` | `refresh(db)` → re-price market-priced assets (deterministic; see Value refresh) |
| `query(db, sql)` → rows, **read-only connection** (`file:…?mode=ro`; a stray `UPDATE`/`DELETE` errors, cannot mutate) | |
| `render_markdown(db)` → the generated `portfolios.md` view | |

The **read-only `query()`** is the open-ended-analysis escape hatch — the
analyst's long tail ("tech exposure ex-retirement", "which lots are long-term
yet") gets full SQL with zero corruption risk. The **views** make the common
rollups answers the model *can't* get wrong. **Writes never go through raw
SQL** — only the validated functions.

## The two surfaces (the experiment)

Both are thin shims over the engine; the only difference is how the model
calls it.

1. **CLI adapter** — `portfolio.py` with `cmd_*` subcommands → engine → JSON on
   stdout. Agent invokes via `bash` after `load_skill: balance-sheet`.
   Matches yfinance/edgar/fred.
2. **Opt-in tool adapter** — engine wrapped as structured tool(s) registered in
   `src/tools/schemas.py` + dispatched in `dispatcher.py`, **unlocked by the
   skill** via frontmatter `tools: portfolio` (the `to_audio` mechanism). The
   model fills a typed schema instead of constructing a shell string — the
   reliability question for a small local model.

**Engine location: under `src/`** (e.g. `src/portfolio/engine.py`) — decided.
The core tool adapter (`src/tools/`) imports it cleanly (core→core, the correct
dependency direction); the CLI adapter in the skill dir imports `src.portfolio`,
which is fine for a skill helper. This keeps the stable core from depending on
optional, persona-gated skill content. Reversible: if the CLI surface wins the
experiment and a self-contained skill is wanted, the engine can be relocated
into the skill dir later.

**How C judges:** C grades at the boundary and anchors via the engine directly,
so it is interface-agnostic. Run the P/T/W suite with the SUT in **CLI mode**,
then **tool mode**, on the local model; compare **pass rate** and **tool-call
efficiency**. The higher-scoring surface ships; the other is dropped.

## Value refresh (deterministic — no LLM in the loop)

Re-pricing holdings is pure data work (`equity = qty × live quote`, gold
`= oz × spot`), so it lives outside the agent loop. One engine `refresh(db)`,
driven two ways; never automatically before a display.

- **Scope of refresh:** only **market-priced classes** (`equity`,
  `physical`/commodity, `crypto`) via `yfin.py`; updates `value` + `value_asof`.
  Illiquid classes (real estate, collectibles, private, cash) have no live feed
  and stay manual `set`.
- **Backbone — scheduled coroutine.** A periodic, market-hours-aware **code
  job** in `run.py`'s TaskGroup (same pattern as the hourly memory-extraction
  coroutine) calls `refresh()` after market close on trading days. **Not** a
  scheduled *agent prompt* — the scheduler triggers `handle()` loops, which
  would put an LLM inside a deterministic job. Code coroutine, no LLM.
- **Agent-triggered.** `refresh` is exposed on the engine surface like any
  other op (CLI command or tool, per the surface experiment). The skill
  instructs: when the owner signals they want current/live/latest values, run
  `refresh` first, then display. The hint-recognition is a skill instruction,
  independent of whether the surface is a tool or CLI.
- **No automatic refresh-before-display** — rejected: it adds a live-quote
  round-trip to every holdings read (latency, esp. on a local model). Routine
  reads show the last refresh; freshness is opt-in via the hint or the schedule.
- **Eval isolation.** `refresh()` runs only against the live
  `context/memory/portfolio.db`, never the C fixture, which stays frozen for
  reproducibility.

## B — the `balance-sheet` skill

- `SKILL.md` teaches: when to use; the data model; the engine surface (active
  adapter); and the **disciplines** — capture cost basis + acquisition date on
  every asset; never hand-sum (run `networth`/`rollup`); never hand-edit the DB
  (use `add`/`set`/`rm`); pick the right `class` so it lands in the right
  bucket; bulk-load a brokerage export via `import_rows` (the LLM maps the
  in-context CSV → schema; the engine validates + self-checks against the
  export's stated account total); when the owner signals they want
  current/live values, run `refresh` before displaying.
- Ships the engine + the active surface adapter(s).
- Added to `personas/finance/persona.yaml` allowlist.
- Name `balance-sheet` — decided. Most scope-accurate (covers assets +
  liabilities + net worth, not just holdings); everyday phrasings ("net
  worth", "track my watch", "my equity") live in the `description`, which is
  what drives routing.

## D — prompt + memory hardening (thin)

- **`personas/finance/prompts/10-domain.md`** — position tracking is
  tool-backed: "never state a net worth or portfolio total you computed by
  hand; use the balance-sheet capability."
- **`personas/finance/prompts/20-guardrails.md`** — (a) **verify-before-cite**
  for private/pre-IPO/rumored facts: WebFetch the top results before stating
  any filing/price/valuation as fact (the SpaceX fix; currently only a
  `core-insights.md` note); (b) capture cost basis + acquisition date whenever
  recording an asset.
- **`context/memory/README.md`** — route asset facts to the tracking
  capability (tool-maintained `portfolio.db`), **not** appended as prose into
  `portfolios.md`; mark the generated `portfolios.md` view "do not hand-edit".

## What does NOT change

- The markdown memory system for prose facts (profile/preferences/insights/
  archives), its extractor, and indexes — untouched except the routing note.
- The other finance skills and the persona's research stack.

## Resolved decisions

1. **Store:** SQLite, engine-as-source-of-truth.
2. **Engine location:** under `src/` (core→core import; CLI helper imports it).
3. **Skill name:** `balance-sheet`.
4. **Surface:** dual (CLI + opt-in tool), judged by C; not an always-on tool.
5. **Helper scope v1:** core read/write/rollup/pnl + `import_rows` (LLM maps
   the in-context CSV → schema; engine validates + account-total self-check) +
   engine `refresh()` agent-triggered on a conversational "current/live/latest"
   hint. No automatic refresh-before-display.
6. **Scheduled refresh coroutine:** **fast-follow**, not v1 — wired to the
   winning surface after the experiment settles.
7. **Migration:** part of B — parse the current dirty `portfolios.md` into
   `portfolio.db` (equity-heavy accounts via `import_rows` from the brokerage
   CSVs in `context/uploads/`; resolve the gold double-count + missing-basis
   gaps with the owner; net worth self-corrects via `v_networth`). Doubles as
   the engine's first real-world test.

## Build sequence

1. SQLite schema + views + the engine module (incl. `import_rows`, `refresh`);
   unit-test the engine directly (matches yfin.py's importable-for-tests
   convention).
2. Both surface adapters (CLI + opt-in tool) over the shared engine.
3. `balance-sheet` SKILL.md + persona allowlist entry; D edits (two prompt
   files + memory README routing note).
4. **Migrate live data** (part of B): `portfolios.md` → `portfolio.db` —
   `import_rows` the brokerage CSVs in `context/uploads/`, hand-resolve the
   gold double-count + missing watch basis with the owner; verify via
   `v_networth`. This is the engine's first real-world test.
5. Coordinate with the C thread: store = SQLite, fixture = seed `.sql`/`.db`,
   anchor runs the views, suite runnable in two SUT-interface modes.
6. Run C in CLI mode and tool mode → pick the surface. (C's before/after is the
   fixture-based baseline vs post-fix run; the live-data migration in step 4 is
   independent of it.)
7. **Fast-follow:** scheduled refresh coroutine in `run.py`, wired to the
   winning surface.

## Open questions for review

- The two carried scope decisions above (helper scope, migration).
