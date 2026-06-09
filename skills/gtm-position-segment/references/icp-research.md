# ICP Research Playbook

Targeted search strategies for deepening understanding of a specific ICP. Use these after the builder has selected ICPs — this is persona-focused research, not broad market research (that was done in onboard-ingest).

## Persona Language & Pain Discovery

```
"{role title}" "{problem keyword}" — how this persona talks about the problem
"{role title}" "{category}" frustrated OR "wish there was" OR "looking for"
site:reddit.com "{role title}" "{problem keyword}" OR "{category}"
site:news.ycombinator.com "{role title}" "{category}"
"{role title}" "my biggest challenge" OR "most time" OR "manual" "{problem domain}"
```

## Community & Channel Discovery

```
"{role title}" community OR slack OR discord OR forum
"{role title}" newsletter OR podcast OR blog follows
"{role title}" conference OR meetup "{industry}"
"best resources for {role title}" OR "where do {role title}s hang out"
```

## Job Posting Analysis

Job postings reveal what the persona's org values, what tools they use, and what language they use.

```
site:linkedin.com/jobs "{role title}" "{tool category}" OR "{problem keyword}"
site:greenhouse.io "{role title}" "{industry}"
site:lever.co "{role title}" "{problem domain}"
"{role title}" job description "{tool or category keyword}"
```

**What to extract from job postings:**
- Tools/platforms listed as requirements (reveals current solution landscape)
- Responsibilities that map to the problem your product solves
- Language used to describe the role (use this in messaging)
- Company size/stage indicators

## Competitor Targeting Analysis

How do competitors specifically target this persona?

```
"{competitor name}" "{role title}" OR "{persona keyword}"
"{competitor name}" case study "{industry}" OR "{company size}"
site:{competitor-domain.com} "{role title}" OR "{persona keyword}"
"{competitor name}" testimonial "{role title}"
```

## Pricing & Buying Behavior

```
"{role title}" budget OR "buying process" OR "procurement"
"{category}" pricing "{company size descriptor}" — e.g., "startup" or "mid-market"
"{role title}" "switched from" OR "moved to" OR "chose" "{category}"
"{category}" ROI OR "business case" OR "justify"
```

## Search Strategy Per ICP

Run searches in this order — each phase informs the next:

1. **Persona language** — how they describe the problem (2-3 queries)
2. **Community discovery** — where they hang out (1-2 queries)
3. **Job postings** — what their world looks like (1-2 queries)
4. **Competitor targeting** — how competitors reach them (1-2 queries per top competitor)
5. **Buying behavior** — how they purchase (1-2 queries)

Total: ~10-15 queries per ICP. Parallelize across ICPs.

## Back-Reference Pass

Before running new searches, re-read source URLs from the product context through the ICP lens:

1. Open the product context's **Ingestion Sources** tables
2. For each Layer 2 source URL:
   - Re-fetch the page
   - Scan specifically for content relevant to this persona
   - Extract quotes, data points, or signals that were missed in the general ingestion pass
3. Pay special attention to:
   - Community threads — filter for posts by people matching the ICP profile
   - Competitor pages — look for persona-specific landing pages or case studies
   - Review platforms — which reviews come from this persona's segment?

This pass often surfaces signal that was in the raw data but didn't make it into the product context summary.

## Platform-Specific Notes

### LinkedIn
- Job postings are the most reliable signal for persona research
- Company pages reveal team size (proxy for segment)
- Posts from people with the target title reveal language and priorities

### Reddit
- Subreddit selection matters more than query precision
- Look for "what do you use for X?" threads — the answers reveal the competitive landscape for this persona specifically
- Flair and post history help confirm the poster matches the ICP

### Twitter/X (via xai-search skill)
- Bio keywords help identify people matching the persona
- Quote tweets of competitor announcements reveal this persona's reactions
- Threads where the persona compares tools are high-signal

### G2/Capterra
- Filter reviews by company size to isolate this persona's segment
- "Switched from" mentions reveal competitive dynamics for this segment
- The "Who uses this?" section on product pages maps persona fit
