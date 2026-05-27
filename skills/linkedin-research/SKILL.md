---
name: linkedin-research
description: "Use when an agent needs LinkedIn profile data, company info, job postings, or professional context for GTM research. Trigger: a skill or task requires founder backgrounds, company positioning, ICP job titles, or buyer language sourced from LinkedIn."
portal_summary: "Look up LinkedIn profiles, companies, and job postings"
---

# LinkedIn Research

Research LinkedIn content via search engine indexes. Requires at least one of `GEMINI_API_KEY`, `XAI_API_KEY`, or `BRAVE_API_KEY`, plus `curl` and `jq`.

LinkedIn aggressively blocks all automated access — headless browsers, scrapers, and direct HTTP requests all hit login walls or 403s. There is no public JSON API. Everything here goes through search engine indexes.

## What's Available vs. Not

**Available via search indexes:**
- Profile summaries (name, headline, current company, bio snippet)
- Company pages (about, employee count range, industry, specialties)
- Job postings (full JD text, requirements, stack mentions)
- Post content (partially — depends on whether Google/Bing indexed the post)

**Not available:**
- Comments, reactions, full post threads
- Connection counts or network graph data
- Messaging or InMail history
- Content behind the login wall (most profile detail past the summary)

## Discovery Methods

Use these in priority order. Prefer Gemini — Google has the deepest LinkedIn index.

### 1. Gemini Grounded Search (best)

```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=$GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "YOUR QUERY about LinkedIn content"}]}],
    "tools": [{"google_search": {}}]
  }' | jq -r '.candidates[0].content.parts[0].text'
```

Query patterns:
- Person: `"[First Last] LinkedIn [Company Name]"`
- Company page: `"[Company] LinkedIn company page about"`
- Scoped: `site:linkedin.com "[Company Name]"`
- Jobs: `site:linkedin.com/jobs "[Role Title]" "[Tool or Category]"`

### 2. xAI Web Search (good alternative)

```bash
curl -s https://api.x.ai/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-4-1-fast-non-reasoning",
    "input": [{"role": "user", "content": "YOUR QUERY about LinkedIn content"}],
    "tools": [{"type": "web_search", "allowed_domains": ["linkedin.com"]}]
  }' | jq -r '.output[] | select(.type == "message") | .content[] | select(.type == "output_text") | .text'
```

### 3. Brave Search (basic)

```bash
curl -s "https://api.search.brave.com/res/v1/web/search?q=site%3Alinkedin.com+YOUR+QUERY" \
  -H "Accept: application/json" \
  -H "X-Subscription-Token: $BRAVE_API_KEY" \
  | jq '.web.results[] | {title, url, description}'
```

Brave returns titles, URLs, and description snippets — useful for discovering which LinkedIn pages exist and getting surface-level summaries, but less synthesized than Gemini or xAI.

## What to Extract for GTM Research

LinkedIn is most valuable for:

| Use case | What to look for |
|----------|-----------------|
| Founder/team credibility | Past companies, titles, tenure, school — signals for buyer trust |
| Competitive intelligence | Company "About" page positioning, employee count, growth signals |
| ICP job postings | Required tools/skills, team structure, pain points encoded in JDs |
| Buyer language | How ICP personas describe their role and problems in their own headlines/bios |

## Job Posting Research Pattern

Job postings are often the single best source for ICP priorities — companies write down exactly what they need, what stack they use, and what problems they're solving. Search patterns:

```bash
# Who is hiring for a role in your category?
site:linkedin.com/jobs "Head of Content" "AI writing"

# What tools do target companies require?
site:linkedin.com/jobs "Marketing Operations" "HubSpot" "Series B"

# What do ICP teams look like?
site:linkedin.com/jobs "Growth Marketing" "[Competitor Name]"
```

Use Gemini grounded search to run these queries — it will synthesize across multiple postings and pull out common themes.

## Tips

- Google indexes LinkedIn most deeply — always try `GEMINI_API_KEY` first.
- Job postings are often more valuable than profiles for GTM research because they encode ICP priorities explicitly.
- Company "About" pages on LinkedIn often contain positioning language that doesn't appear on the company's own website (e.g., how they describe their category to job seekers).
- Always include the company name when searching for a person — there are too many false matches otherwise.
- At least one of `GEMINI_API_KEY`, `XAI_API_KEY`, or `BRAVE_API_KEY` must be set.

## Common Mistakes

- **Trying Playwright or shot-scraper** — LinkedIn will block headless browsers and may flag the IP. Don't attempt direct scraping.
- **Using web_fetch or curl directly on linkedin.com URLs** — returns a 403 or login redirect, not content.
- **Expecting full post threads** — only the indexed portion of a post is accessible. Comments and reactions are not.
- **Not specifying company when searching for people** — `"Jane Smith LinkedIn"` returns hundreds of false matches. Use `"Jane Smith LinkedIn Acme Corp"`.
- **Assuming job postings are current** — LinkedIn jobs in search indexes may be expired. Note posting dates when they're available, and treat the content as signal about team priorities rather than active openings.
