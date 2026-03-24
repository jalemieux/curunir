# Curunir Landing Page Design Spec

## Overview

A landing page to validate interest in Curunir — a hosted, secure, non-technical alternative to OpenClaw. The first GTM vertical is digital marketing. The page is a **beta waitlist signup**, not a product launch.

## Positioning

**Core pitch:** "Like OpenClaw, but simple and secure."

Curunir rides the OpenClaw hype wave by acknowledging that non-technical users want AI agent capabilities but can't self-host, configure via CLI, or manage their own security. Curunir is the hosted, managed, accessible version.

**Target audience:** Non-technical business users — especially digital marketers — who have heard about OpenClaw / AI agents but find them too technical to use.

**CTA:** Beta waitlist email capture (no pricing, no login, no trial).

---

## Research Summary: YC B2B Landing Page Patterns

Based on analysis of 200+ YC startup landing pages from W24, S24, and W25 batches:

### Aesthetics

- **Dark mode with vibrant accents** — dominant for developer/agent tools (Kapa.ai, Vercel). Signals technical sophistication.
- **Light/neutral with warm accents** — approachable, non-intimidating (Firecrawl: burnt orange on off-white, Pulley: cream with navy).
- **High-contrast monochrome** — authority and simplicity (Deel: black/white with purple).
- **Typography:** Geist, Inter, or custom display fonts. Monospace for code snippets. Bold serif headlines returning for differentiation. Hero headlines at 44-64px.
- **Spacing:** Generous whitespace, 4px/8px unit systems, centered content with ~1200px max-width.

### Page Structure (universal pattern)

Hero → Trust Block → Feature Block → Social Proof → Supporting Blocks → Final CTA

### Key Patterns

- **Centered hero layout** dominates over side-by-side
- **Eyebrow text** above headline (funding, launches, badges)
- **Dual CTAs** — primary bold + secondary ghost button
- **Product-as-hero visual** — show the actual product, not abstract illustrations
- **Before/After comparisons** — particularly effective for tool consolidation
- **Bento grids** — mixed-size feature cards (Vercel pattern)
- **Logo bars** — ~50% use auto-scrolling carousels
- **Specific metrics** in social proof ("80,000+ companies", "20x ROI")
- **Contextual testimonials** placed alongside relevant features

### Sources

- Evil Martians: "We Studied 100 Dev Tool Landing Pages" (2025)
- SaaSFrame: "10 SaaS Landing Page Trends for 2026"
- The Branx: "Best Tech Startup Websites of 2025"
- New Economies: "Ultimate Guide to YC Startup Landing Pages"

---

## Template A: "The Challenger"

**Aesthetic:** Dark mode with purple accents (Kapa.ai / Vercel pattern)
**Tone:** Bold, direct. Names OpenClaw explicitly.
**Best for:** OpenClaw-aware audience (HN, Twitter, Reddit traffic)

### Color Palette

- Background: `#0a0a0a` → `#1a1a2e` (gradient)
- Primary accent: `#6c5ce7` (purple)
- Secondary accent: `#a29bfe` (light purple)
- Text: `#ffffff` (primary), `rgba(255,255,255,0.6)` (secondary)

### Page Sections

1. **Nav** — Logo ("🦞 Curunir") + "How it works" link + "Join the beta" button
2. **Eyebrow badge** — "✨ Now accepting early access signups"
3. **Hero**
   - Headline: "Like OpenClaw. But simple and secure."
   - Subhead: "The AI agent that actually does things — without the terminal, the self-hosting, or the risk."
   - Inline email capture: `[your@email.com] [Get early access]`
4. **Product demo** — Chat-style UI showing a marketing task being executed (e.g., "Find 50 fintech leads and draft cold emails" → step-by-step completion)
5. **How it works** — 3 steps: Sign up → Connect your apps → Start chatting
6. **Before/After** — OpenClaw CLI setup (red, painful) vs Curunir (green, simple)
7. **Use cases grid** — 3 cards: Marketing, Sales, Operations
8. **Trust signals** — Security badges: SOC 2, EU hosted, end-to-end encrypted, no data training
9. **Final CTA** — Repeat email capture with contrasting background

---

## Template B: "The Friendly One"

**Aesthetic:** Light cream with warm orange accent (Firecrawl / Pulley pattern)
**Tone:** Warm, approachable. Anti-developer aesthetic. Subtle OpenClaw reference.
**Best for:** Non-technical newcomers who've never used an AI agent

### Color Palette

- Background: `#faf9f7` (warm off-white)
- Primary accent: `#e8630a` (burnt orange)
- Text: `#1a1a1a` (primary), `rgba(0,0,0,0.5)` (secondary)
- Success: `#27ae60`, Error: `#c0392b`

### Page Sections

1. **Nav** — Logo ("Curunir") + "Features" link + "Join waitlist" button
2. **Hero**
   - Headline: "An AI agent that works for you."
   - Subhead: "All the power of OpenClaw. None of the complexity."
   - Supporting text: "Email, scheduling, research, outreach — just tell it what you need in plain English."
   - Email capture: `[Enter your email] [Join waitlist]`
   - Anti-spam note: "🔒 No spam. Early access + product updates only."
3. **Before/After panel** — Side-by-side: "Other AI agents" (self-host, terminal, Docker, manage security) vs "Curunir" (sign up, connect apps, chat, we handle security)
4. **Integration logo bar** — Gmail, Slack, WhatsApp, HubSpot, Notion, Calendar
5. **Use cases** — 3 cards: Marketing, Sales, Operations (with concrete examples)
6. **"What is an AI agent?" explainer** — Short educational section for total newcomers. Explains the concept simply.
7. **How it works** — 3 simple steps with illustrations
8. **Security & trust section** — Hosted, encrypted, compliant
9. **Final CTA** — Waitlist with social proof count ("Join 1,200+ people on the waitlist")

---

## Template C: "The Premium One"

**Aesthetic:** Deep navy gradient with purple accents (Deel / Pulley pattern)
**Tone:** Premium, aspirational, exclusive. Creates FOMO.
**Best for:** Both audiences. "Request access" framing creates urgency.

### Color Palette

- Background: `#0f0f23` → `#1a1a3e` (gradient)
- Primary accent: `#6c5ce7` → `#a29bfe` (gradient)
- Text: `#ffffff` (primary), `rgba(255,255,255,0.5)` (secondary)

### Page Sections

1. **Nav** — Logo ("Curunir") + "How it works" + "Security" links + "Request access" button (gradient)
2. **Split hero** (text left, chat mockup right)
   - Eyebrow: "EARLY ACCESS" (uppercase, purple)
   - Headline: "You've seen what OpenClaw can do. Now you can too." ("Now you can too" in purple)
   - Subhead: "No servers. No terminal. No risk. Just tell your AI agent what to do."
   - Email capture: `[you@company.com] [Request access]`
   - Social proof: "Join 2,400+ on the waitlist"
   - **Right side:** WhatsApp chat mockup showing a real marketing task (e.g., "Analyze our blog traffic and suggest 10 content ideas for Q2" → agent responds with completed analysis)
3. **Three value pillars** — 🔒 Secure (end-to-end encrypted) · 💬 Simple (chat from any app) · ⚡ Powerful (full agent capabilities)
4. **Video demo or animated walkthrough**
5. **Feature bento grid** — 4-6 capabilities in mixed-size cards
6. **Security deep-dive section** — detailed trust/compliance info
7. **Early testimonials / tweets**
8. **"Built for" persona cards** — Marketers, Founders, Ops teams
9. **Final CTA** — Email capture with waitlist counter

---

## Mix & Match Options

Elements can be combined across templates:

- Hero from C ("You've seen what OpenClaw can do") + Before/After from B
- Dark theme from A + "What is an AI agent?" explainer from B
- Waitlist counter from C + warm color palette from B
- Split hero from C + product demo chat UI from A

---

## Technical Notes

- **CTA everywhere:** Email capture in hero + repeated at bottom
- **No pricing, login, or trial** — pure interest validation
- **Mobile-first:** All templates should be responsive
- **Analytics:** Track email signups, scroll depth, time on page
- **Stack TBD:** Next.js, Astro, or simple static HTML — to be decided during implementation planning
