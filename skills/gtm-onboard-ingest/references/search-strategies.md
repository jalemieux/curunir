# Search Strategies Reference

Detailed query patterns for Layer 2 market research. Use these as starting points — adapt based on what Layer 1 reveals about the product's category, problem domain, and keywords.

## Query Templates

### Competitive Discovery

```
"{product name}" alternatives
"{product name}" vs
"alternatives to {product name}"
"best {category} tools"
"best {category} software"
"{category} comparison {current year}"
"{category} tools for {target persona}"
"{problem keyword}" solution
"{problem keyword}" tool
"{problem keyword}" software
```

### Community & Buyer Voice

```
site:reddit.com "{category}" OR "{problem keyword}" recommendation
site:reddit.com "best {category}" OR "looking for {category}"
site:news.ycombinator.com "{product name}" OR "{category}"
site:news.ycombinator.com "Show HN" "{category}"
"{problem keyword}" frustrated OR "wish there was" OR "looking for"
"{category}" review honest OR "my experience"
```

### X/Twitter Social Listening (via xai-search skill)

```
"{product name}" — direct mentions
"{competitor name}" complaint OR issue OR bug OR "switched from"
"{category}" recommendation OR suggest OR "anyone know"
"{problem keyword}" — how buyers describe the pain
"{product name}" OR "{competitor}" love OR hate OR "switched to"
```

### Review Platforms

```
site:g2.com "{category}"
site:capterra.com "{category}"
site:trustradius.com "{category}"
site:producthunt.com "{category}" OR "{product name}"
"{product name}" review
"{competitor name}" review
```

### Pricing Intelligence

```
"{competitor name}" pricing
"{competitor name}" pricing page
"{category}" pricing comparison
"{category}" cost OR price OR "how much"
"{category}" free tier OR free plan OR open source alternative
```

### SEO & Demand Signals

```
"how to {problem the product solves}"
"best way to {problem the product solves}"
"{problem keyword}" guide OR tutorial OR "how to"
"People Also Ask" — note related queries in search results
"{category}" market size OR growth OR trend
```

### Competitor Intelligence

```
"{competitor name}" blog OR changelog OR "what's new"
site:linkedin.com/company "{competitor name}" — employee count
"{competitor name}" hiring OR careers OR "we're hiring" — reveals investment areas
"{competitor name}" funding OR raised OR series — reveals stage and trajectory
```

## Buyer-Segment-Specific Queries

When the confirmed buyer is a non-obvious segment (not the default "tech professional"), add queries that match their language and communities. The standard query templates above assume the buyer searches in category terms ("best X tool"). Real buyers often search in problem terms specific to their context.

### How to adapt

After Step 1.5 (Buyer Hypothesis Check), identify:
1. **What language does this buyer segment use?** (not industry jargon — their actual words)
2. **Where does this buyer segment gather online?** (specific subreddits, forums, Facebook groups, WhatsApp communities)
3. **What adjacent tools does this buyer already use?** (reveals discovery channels)

### Example: ESL / non-native English speakers

```
"non-native speaker" tool OR app OR AI
"write like a native" tool OR app
"sound native" English tool
"help me write English" message OR email
"English writing help" "second language" OR "non-native"
site:reddit.com r/EnglishLearning tool OR app OR recommend
site:reddit.com r/languagelearning writing help OR tool
"afraid to write" English OR email
"embarrassed" English writing OR grammar
```

### Example: Small business owners (non-technical)

```
"{category}" "small business" OR "solo" OR "freelancer"
"how do I {problem}" without OR simple OR easy
site:reddit.com r/smallbusiness "{category}" OR "{problem keyword}"
site:reddit.com r/Entrepreneur "{category}" recommend
"{problem keyword}" "no technical" OR "non-technical" OR beginner
```

### Example: Students / academics

```
"{category}" student OR academic OR research
site:reddit.com r/GradSchool "{problem keyword}" OR "{category}"
site:reddit.com r/AskAcademia "{problem keyword}" tool
"{problem keyword}" "PhD" OR "dissertation" OR "thesis"
"{category}" free OR student discount
```

**Add your own segment patterns** based on what Layer 1 reveals about the actual buyer. The key insight: the buyer's search language is often very different from how the builder or competitors describe the category.

## Platform-Specific Search Notes

### Reddit
- **Reddit blocks automated web_fetch requests (403).** Use `playwright` skill (headless browser) for direct access. If unavailable, rely on search engine cached results or secondary aggregators.
- Search within relevant subreddits, not just site-wide
- Recommendation threads ("what do you use for X?") are gold
- Sort by relevance and by recent — both matter

### Hacker News
- Use Algolia HN search (hn.algolia.com) for better results
- "Show HN" posts in the category reveal direct competitors
- Comment threads often contain deeper analysis than the post itself

### G2/Capterra
- Category pages list all competitors in one place
- Review text reveals buyer language and pain points
- Star ratings are less useful than the actual review content
- "Switched from" mentions in reviews reveal competitive dynamics

### Product Hunt
- **Product Hunt frequently returns 403 on automated fetches.** If the builder mentions a PH launch, ask for the direct URL rather than searching. Product names are often shared across unrelated products on PH.
- Launch comments reveal early reception and buyer skepticism
- "Maker" responses show how the builder positions
- Related products section maps adjacent competitors

### Twitter/X
- **Requires xAI API via `xai-search` skill for reliable access.** Invoke the skill and use `x_search` tool type. Do not skip social listening — it's a required research channel.
- Recent tweets (< 30 days) are most valuable for current sentiment
- Quote tweets of product announcements reveal reactions
- Threads where people compare tools are high-signal

### Medium
- Many articles are paywalled or return 403. Use `playwright` skill for better access. Note when articles couldn't be retrieved.

### LinkedIn
- Content is login-walled. Use search engine cached results. Note the limitation.

### Quora
- Dynamic content often returns 403. Question titles from search results are often sufficient signal — the question itself reveals how buyers frame the problem.

## Search Strategy Sequencing

The order matters because each phase informs the next:

1. **Product name + category** → identifies direct competitors
2. **Competitor names** → fan out to their sites, reviews, mentions
3. **Buyer-segment-specific queries** → find the actual buyer's communities and language
4. **Problem keywords** → find buyer communities and language
5. **Buyer-intent keywords** → map SEO landscape and content gaps
6. **Pricing queries** → complete the competitive picture

Don't run all queries upfront. Let early results inform later searches. If Phase 1 reveals a competitor you didn't know about, add their name to the query list for remaining phases.
