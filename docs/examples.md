# What Curunir Can Do

Curunir is a personal assistant you talk to over chat or email. It comes with
a set of **skills** — small markdown instructions that unlock specific
capabilities on demand. You don't call skills directly; you just ask for what
you want, and Curunir loads the right one.

This doc walks through the skills that ship today and shows a prompt you can
copy-paste to try each one.

---

## Research the web

**`web-search`** — Plain web search via Brave.

> Search the web for "best Python HTTP client in 2026" and show me the top 5
> results.

**`gemini-search`** — Google-grounded search and YouTube video summarization.

> Summarize this YouTube talk and give me three takeaways: `<url>`

**`xai-search`** — Search the web *and* X/Twitter through Grok. Good for
real-time reactions.

> What's the X reaction to Apple's latest announcement? Last 24 hours only.

**`reddit-research`** — Find what real users are saying on Reddit.

> What are Reddit users complaining about with Notion lately? Pull 5 recent
> posts and summarize the common gripes.

**`linkedin-research`** — Founder/company/job research via search indexes.

> Give me a one-paragraph brief on the founders of `acme-labs.com`. I'm
> meeting them Thursday.

**`playwright`** — Fetch JS-rendered pages, take screenshots, pull structured
data from sites that plain HTTP can't see.

> Take a screenshot of `news.ycombinator.com` and tell me the top story.

---

## Do deep research

**`deep-research`** — Orchestrates the search skills above into a structured
report, delivered as a PDF attachment.

> Do a deep research pass on "passkeys adoption in consumer apps". Pull from
> web and Reddit, cite sources, and attach the PDF.

What happens: Curunir decomposes the topic, loads the right prerequisite
skills (always `web-search`, plus others based on the topic), runs searches,
writes a cited report, and renders it to PDF.

**`fact-checker`** — Independently audits a research report or a set of
claims. Runs in isolation so it isn't biased by the original reasoning.

> Fact-check the passkeys report you sent yesterday. Flag anything that isn't
> independently verifiable.

---

## Analyze public companies

**`yfinance`** — Fundamentals, prices, multiples, peers from Yahoo Finance
(no API key).

> What's Eli Lilly's current P/E and how has it moved over the last 5 years?

**`fred`** — US macro time-series from the St. Louis Fed (interest rates,
CPI, GDP, unemployment, FX). Requires `FRED_API_KEY`.

> What's the current 10-year Treasury yield, and where is core CPI YoY?

**`sec-edgar`** — Official 10-K, 10-Q, 8-K filings and standardized XBRL
fundamentals (no API key, but `SEC_USER_AGENT` must be set).

> Pull Eli Lilly's last three 10-Ks and tell me how segment revenue has
> shifted.

**`financial-analysis`** — Orchestrates the three above into a structured
analysis: scenario modeling, multiple-based valuation, peer comparables,
and sensitivity. Delivered as a markdown report inline + PDF attachment.

> Do a financial analysis of Eli Lilly assuming the new drug adds $30B in
> annual revenue. Show me base/bull/bear scenarios with implied price at
> peer-set multiples, and which assumptions matter most.

What happens: Curunir loads `financial-analysis`, pulls current financials
from `yfinance`, cross-checks revenue from `sec-edgar`, picks 3-5 real
peers (PFE, MRK, NVO, BMY, JNJ), runs the four-framework workflow, and
attaches the PDF.

---

## Work with GitHub

**`github`** — Ad-hoc operations: file an issue, list PRs, comment, search
repos.

> File an issue in `jalemieux/curunir` titled "Add hot-reload for skills" with
> a short description of why it matters.

**`git-contribute`** — Full lifecycle: claim an open issue → propose a plan as
a draft PR → wait for review → implement test-first → merge.

> Pick up the oldest open issue in `jalemieux/curunir` and move it one phase
> forward.

Run it on a schedule (see below) and Curunir chips away at your backlog while
you sleep.

---

## Remember things across conversations

**`extract-learnings`** — Paste a blob of notes or a Slack catch-up and
Curunir pulls out the durable parts, filing them into `context/memory/`.

> Here are my notes from today's standup: <paste>. Extract anything worth
> remembering long-term.

Memory isn't a special skill — it's always on. Try this in a *new* session
after using `extract-learnings`:

> What did I decide about the auth migration last week? Check memory first.

---

## Send email to other people

**`email-send`** — Send a *new* outbound email to someone who isn't already in
the thread you're chatting on. Useful from the CLI or from scheduled tasks.

> Draft a short intro email from me to `jane@acme.com` letting her know we met
> at the conference and proposing a 20-minute call next week. Send it.

This skill is **disabled by default** because it can reach people on your
behalf without a human in the loop. Turn it on explicitly when you want that
capability — remove `disabled: true` from `skills/email-send/SKILL.md` (or
just tell Curunir "enable the email-send skill" and it will do it for you).

Note: if Curunir is already replying on an inbound email thread, you don't
need this skill — the email channel sends its final response automatically.

---

## Build new skills

**`skill-factory`** — If you can describe a workflow in prose, Curunir will
build a new skill for it.

> Build me a skill called `daily-journal` that asks me three questions each
> morning — what I'm grateful for, what I'm focused on, what I'm avoiding —
> and appends the answers to `context/memory/journal/YYYY-MM-DD.md`.

The resulting skill lands in `skills/daily-journal/SKILL.md` and is available
on next start.

---

## Put any of the above on a schedule

Scheduling isn't a skill — it's a built-in tool. But it's how you turn any of
the skills above into an always-on behavior.

> Every weekday at 8am, do a deep-research pass on "new model releases from
> Anthropic or OpenAI in the last 24h" and only email me if there's genuinely
> new signal.

> Every Sunday at 6pm, read my memory and email me a short reflection on what
> I worked on this week.

Scheduled sessions start fresh (no prior chat), so Curunir writes the prompts
to carry their own context. You just describe the outcome.

---

## How to Interact

**CLI (fastest loop):**

```bash
python cli.py --host localhost
```

Then just type. Curunir will load whichever skill fits what you asked for —
you don't name the skill, you describe what you want.

**Email (the one you set up with Gmail):**

Send a message to the delegated address. Replies thread normally. Good for
kicking off a research pass from your phone and reading the result in your
inbox an hour later.

**Tip:** if you want to see what skills Curunir has at any moment, just ask:

> What skills do you have loaded? List them with one line each.
