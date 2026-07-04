# Skill Suites

Per-skill (or skill-family) graded eval suites. Each grades **one skill's
contract in depth** — method adherence, failure modes, output rules — and runs
against any persona whose allowlist carries the skill.

What these suites deliberately do **not** own is **routing**: "does natural
phrasing reach the skill at all?" is a property of the persona's catalog
(collision/shadowing among the skills that persona ships), so the routing
tripwires live in the persona suites — `eval/default/` (WS*/RR* + the RS
sweep) and `eval/finance/` (R1/FR*/PM*). See the taxonomy in
[`eval/README.md`](../README.md).

| Suite | Skill(s) | Contract graded |
|-------|----------|-----------------|
| [`skill_routing/`](skill_routing/) | `yfinance` / `fred` / `polymarket` | adherence: freshness citations, smallest subcommand, the CPI trap, priced probabilities |
| [`reddit_research/`](reddit_research/) | `reddit-research` | method: curl the JSON API, never `web_fetch` a Reddit URL; grounded synthesis |
| [`web_search/`](web_search/) | `web-search` | method: Brave first, no blocked-site rediscovery loop; grounded, search-then-fetch |

(`skill_routing/` keeps its historical name; post-migration it is the
live-data **adherence** suite.)

Every suite is a thin shim over the shared engine in
[`eval/harness/`](../harness/): a `<name>_tasks.py` data file plus a
`run_<name>_evals.py` that builds a `SuiteConfig` and calls `runner.main`.
