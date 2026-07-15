---
name: gtm
description: "Front door for go-to-market work — use for ANY natural GTM request that doesn't already name a specific pipeline stage. Trigger: the user wants help with go-to-market / GTM, launching a product, positioning or repositioning, messaging or copy, pricing, ICP / segmentation / who-to-sell-to, competitors or competitive landscape, demand validation, a GTM plan or strategy, onboarding a product, or asks you to review / critique / improve a landing page, homepage, positioning, or marketing assets. Classifies the request and loads the right gtm-* stage skill(s). Skip this and go straight to the stage skill only when the user explicitly names a pipeline stage they're already mid-flow on."
portal_summary: "Go-to-market help — positioning, messaging, pricing, ICP, competitors, launch plans, and landing-page critique."
portal_starter: true
---

# GTM Router

The go-to-market front door. The discrete `gtm-*` stage skills trigger on
pipeline-stage vocabulary ("finalized positioning", "product context doc"),
so a request phrased in everyday terms — "help me with go-to-market", "review
my landing page", "who should I be selling to?" — routes to none of them. This
skill catches that broad intent, figures out what the user actually needs, and
hands off to the right stage skill(s).

**This skill does no GTM work itself.** It classifies and routes. The stage
skills remain the single source of truth for each stage's behavior — read them
in full via `load_skill` and follow them.

## The stage skills you route to

| Skill | What it does | Reach for it when the user wants… |
|-------|--------------|-----------------------------------|
| `gtm-onboard-ingest` | Builds a deep product-context doc from published materials + market research + a builder conversation. The pipeline's entry point. | …to start GTM for a product, "onboard my product", deep product understanding, or there's no product-context doc yet and downstream work needs one. |
| `gtm-position-segment` | Produces prioritized ICP cards with messaging and pricing signals. Needs a product context. | …ICP / segmentation / "who do I sell to", messaging, value prop, pricing positioning, repositioning. |
| `gtm-plan` | Turns confirmed positioning into an actionable GTM plan — channels, actions, sequencing, metrics. | …a GTM plan / strategy, launch plan, channel plan, "what do I actually do", a roadmap to market. |
| `gtm-competitive-landscape` | Comprehensive competitive research — direct + buyer-segment + incumbent competitors, pricing, sentiment, moat. | …competitive analysis, "who are my competitors", competitive landscape, moat / differentiation framing. |
| `gtm-competitive-monitor` | Delta scan against an existing competitive-landscape doc — what changed since last check. | …to track competitor moves over time, "what's changed", a periodic/scheduled competitive check (requires a prior landscape doc). |
| `gtm-reassess` | Evaluates new intel (a competitor move, pricing/feature change, market shift) against existing GTM docs and updates them. | …to fold new information into existing product-context / positioning / plan docs. |
| `gtm-smoke-test` | Fake-door demand validation — real buyers attempt to transact. For ideas without a finished product. | …to validate demand for an idea, test purchase intent, "will anyone buy this" before building. |

## How to route

1. **Classify the request.** Map what the user is asking for to the table above.
   Use their words, not the pipeline's — "who's my customer" → segmentation,
   "what should the homepage say" → messaging/positioning, "is this worth
   building" → smoke test.

2. **Load the matching stage skill(s) with `load_skill` and follow them.**
   For a single, clearly-staged request, load the one matching skill.

3. **Orchestrate when the request is compound or cross-cutting.** You MAY load
   **more than one** stage skill and synthesize across them — this is a router,
   not a single-skill lock. Trust your judgment about which part of which skill
   applies. Examples:
   - "Help me with go-to-market" with nothing built yet → likely
     `gtm-onboard-ingest` first; tell the user the pipeline continues into
     `gtm-position-segment` then `gtm-plan`.
   - "Critique my positioning vs. my competitors" → combine
     `gtm-position-segment` (positioning/messaging framing) and
     `gtm-competitive-landscape` (competitor framing).

4. **Respect mid-pipeline flow.** If the user is clearly already working a
   specific stage (they reference a finalized positioning doc, a product-context
   file, etc.), route straight to that stage skill and don't second-guess it —
   the stage skill's own narrower trigger is correct there.

## Gap cases: landing-page / asset critique

There is **no** dedicated frontend- or landing-page-critique skill, and you
should **not** declare such a request out of scope. When the user asks you to
**review, critique, or improve a landing page, homepage, hero copy, or other
marketing asset**, compose the critique from whichever stage skills carry the
relevant lens, e.g.:

- **Positioning & messaging** — load `gtm-position-segment` for the ICP,
  value-prop, and messaging frame: does the page speak to the right buyer with
  the right message and price signal?
- **Competitive differentiation** — load `gtm-competitive-landscape` (or an
  existing landscape doc) to judge whether the page's claims actually
  differentiate against competitors and incumbents.
- **Channel/CTA fit** — load `gtm-plan` thinking when the question is about
  conversion path, CTA, or how the page fits the broader launch.

Pull the framing you need from those skills and synthesize a concrete critique.
The goal is to give a useful answer from the existing catalog, never to bounce
the request because no skill is named "landing-page-critique".
