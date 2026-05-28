---
name: superheroes
description: "Answer kid-friendly superhero questions and run head-to-head battles between heroes/villains, grounded in Wikipedia + SuperHero API (superheroapi.com). Use when the user (or their kid) asks things like 'tell me about Spider-Man', 'what are Iron Man's powers', 'who would win between Thor and Superman', 'Marvel vs DC', or any 'X vs Y' superhero matchup. Mode A is a lookup/lore card; Mode B is a narrative battle write-up. Always cite the Wikipedia + SuperHero DB pages used — do not invent canonical facts."
portal_summary: "Look up superheroes and run head-to-head battles — kid-friendly"
portal_starter: true
tools: attach
---

# Superheroes

A kid-facing skill for two related jobs:

- **Mode A — Lookup / lore.** Build a short, source-cited "hero card" for a character (powers, gear, allies, villains, famous storyline).
- **Mode B — Battle.** Tell the story of a matchup between two or more characters — who wins, why, what makes the fight interesting.

Both modes share the same sources, the same tone rules, and the same fetcher policy. Always run a Mode A pass on every combatant before answering a battle question — you can't write the fight if you don't know what they can do.

## Tone (applies to every response)

Write for a kid (the user's son is the consumer).

- Short sentences. Plain words. Curious, fun energy.
- No gore, no graphic violence, no killing-blow language. Combatants are knocked out, webbed up, sent home — never gutted, dismembered, or killed.
- No real-world political analogies, no adult references, no mature comics arcs (Punisher MAX, post-Identity Crisis DC, etc. — keep to mainline continuity).
- "Wow that's cool" beats "well actually." If a power is unusual, lean into why it's cool, not into power-scaling forum debates.

## Sources

Canonical facts come from two JSON APIs, called at runtime via `curl + jq` from a `bash` tool call. Marvel.com, DC.com, and the fandom wikis are *not* fetched directly — their user-agent gating returns 403 for non-browser clients. The APIs cover the same canonical data.

- **Wikipedia REST + MediaWiki API** — biographical prose, first-appearance, publication history, and the **Powers and abilities** section per character. Free, no auth, never blocked.
- **SuperHero API** (superheroapi.com) — structured fields (real name, first appearance, team affiliations, occupation) and the 6-stat rubric Mode B needs (intelligence, strength, speed, durability, power, combat). Free tier, requires a token in `SUPERHERO_API_TOKEN`.

For the user-facing `**Sources:**` line at the bottom of each card, cite the human-readable pages, not the API URLs:

- `https://en.wikipedia.org/wiki/{Hero}` (the Wikipedia article)
- `https://www.superherodb.com/{slug}/10-{id}/` (the SuperHero DB character page, only when stats were used)

**Out of scope.** TikTok/Reddit/YouTube power-scaling debates, fan-fiction sites, AI-generated character pages. If the user asks "what does this TikTok say about X vs Y", you can summarize what the *clip* claims, but mark it `(non-canon, fan opinion)` and don't let it drive the verdict.

If a fact can't be confirmed against the APIs, mark it `(unconfirmed)` in the output rather than guess. Better to leave a blank than to fabricate a first-appearance date.

## Fetcher policy

Three `curl + jq` calls per character, all from `bash` tool calls. Run them in parallel where possible (separate `bash` tool calls in the same turn). Never use `WebFetch` on marvel.com / dc.com / fandom / superherodb.com — those user-agents are blocked.

### 1. Wikipedia summary — bio + publication prose

```bash
curl -sS "https://en.wikipedia.org/api/rest_v1/page/summary/{NAME}" \
  | jq -r '.title, .description, .extract'
```

Returns the article lead (1–3 paragraphs covering creator, first appearance, publication history). Use for the **Famous for** line on the Mode A card. If the article 404s, retry with disambiguation: `{NAME}_(character)`, `{NAME}_(comics)`, `{NAME}_(Marvel_Comics)`, `{NAME}_(DC_Comics)`.

### 2. Wikipedia "Powers and abilities" section

Two-step. First list sections to find the index:

```bash
curl -sS "https://en.wikipedia.org/w/api.php?action=parse&page={NAME}&prop=sections&format=json" \
  | jq -r '.parse.sections[] | "\(.index)\t\(.line)"'
```

Find the line that says `Powers and abilities` (variants exist: `Powers`, `Abilities`, `Powers, abilities, and equipment`). Take its index, then:

```bash
curl -sS "https://en.wikipedia.org/w/api.php?action=parse&page={NAME}&section={INDEX}&prop=wikitext&format=json" \
  | jq -r '.parse.wikitext."*"'
```

Returns the section as wikitext. Ignore `<ref>…</ref>` and `{{…}}` template markup; the facts are in the surrounding prose. Distill into 3–7 kid-friendly bullets for the card. If no power-related section exists, note `(powers list partial — Powers section not found on Wikipedia)` and distill what you can from the summary prose.

### 3. SuperHero API — structured fields + stats

Only if `SUPERHERO_API_TOKEN` is set. Search by name to get the ID:

```bash
curl -sS -L "https://superheroapi.com/api/$SUPERHERO_API_TOKEN/search/{NAME}" \
  | jq '.results[] | {id, name, "full-name": .biography["full-name"], "first-appearance": .biography["first-appearance"]}'
```

Pick the entry whose name exactly matches and isn't an "Evil X" / "Venom X" variant. Cross-reference `full-name` against the Wikipedia summary to disambiguate (e.g., multiple "Spider-Man" entries → pick the one whose `full-name` matches "Peter Parker"). Then fetch the full record:

```bash
curl -sS -L "https://superheroapi.com/api/$SUPERHERO_API_TOKEN/{ID}" \
  | jq '{powerstats, biography, connections, work, image}'
```

**Trust order.** Wikipedia for first-appearance dates and publication history; SuperHero API for the 6-stat rubric and team affiliations; both confirm real name. Do **not** trust SuperHero API's `biography.publisher` field — it's frequently mislabeled (Deadpool's reads "Evil Deadpool"). Infer publisher from the Wikipedia article instead (`"published by Marvel Comics"` / `"published by DC Comics"` in the extract is reliable).

### Graceful degradation

- **No `SUPERHERO_API_TOKEN` in env**: skip call 3. Mode A still works (the **Stats** table is omitted, team list is shorter, no `(stats unavailable)` row in Mode B). At the end of the turn, tell the user once how to add it: free signup at superheroapi.com → set `SUPERHERO_API_TOKEN` in `.env`. Don't nag on every query.
- **Wikipedia article doesn't exist**: try the disambiguation suffixes listed above; if still 404, mark first-appearance + publication history `(unconfirmed)` and proceed.
- **SuperHero API returns no matches**: rare but possible for obscure characters; proceed Wikipedia-only and note `(stats unavailable — not in SuperHero DB)`.
- **Multiple SuperHero matches**: prefer the entry whose `full-name` matches Wikipedia; if still ambiguous, ask the user which version.

## Mode A — Lookup / lore

**Trigger phrases:** "tell me about", "who is", "what are X's powers", "first appearance of", "X's villains".

**Steps:**

1. **Identify** character + universe. Marvel or DC. If the user named a specific continuity (Ultimate, Earth-616, Injustice, Spider-Verse), honor it; otherwise default to main continuity (Earth-616 for Marvel, main DC continuity for DC).
2. **Fetch** the data via the three Fetcher policy calls — Wikipedia summary, Wikipedia Powers section, SuperHero API. Run the Wikipedia and SuperHero calls in parallel (two `bash` blocks in the same turn); the Powers section fetch is sequential after the section-list call.
3. **Render** the kid-friendly card below. Cite every source inline. Mark uncertain facts `(unconfirmed)` instead of guessing.

**Card template:**

```markdown
# {Hero Name} ({Publisher})

**Real name:** …
**First appeared:** {Title} #{Issue} ({Year})
**Team:** {Avengers / Justice League / X-Men / solo / …}

**Powers:**
- {3–7 bullets, plain English. "Super strong" beats "class-100 strength."}

**Gear:** {signature suit, weapons, vehicles — short list}

**Famous for:** {1–2 sentences on a famous, kid-appropriate storyline}

**Friends:** {2–4 key allies}
**Enemies:** {2–4 key villains}

**Stats** (from SuperHero DB, divided by 10):
| Intelligence | Strength | Speed | Durability | Power | Combat |
|---|---|---|---|---|---|
| {1–10} | {1–10} | {1–10} | {1–10} | {1–10} | {1–10} |

**Sources:** [Wikipedia](…), [SuperHero DB](…)
```

The **Stats** row is included on Mode A cards only when the SuperHero API call succeeded. If the token is missing or the character isn't in SuperHero DB, omit the row entirely (don't print a row of `(unconfirmed)` values).

## Mode B — Battle (narrative)

**Trigger phrases:** "who would win", "X vs Y", "battle", "fight", "Marvel vs DC".

**Steps:**

1. **Lookup pass.** Run Mode A internally on every combatant first. You don't have to print the cards (unless the user asked), but you do need the canonical powers / gear / weaknesses in hand before writing the fight.
2. **Pull stats** from the SuperHero API record fetched during the Mode A pass on each combatant (the 6-stat block under `.powerstats`, values 0–100 — divide by 10 for the 1–10 rubric). Use them as *grounding* — to spot the real gap in the matchup — not as a scoreboard. You don't have to print the table unless it adds something. If `SUPERHERO_API_TOKEN` isn't set, skip the rubric and ground the matchup on the Wikipedia Powers prose instead; call out `(stats unavailable — token not set)` once.
3. **Pick a venue / scenario.** Default: neutral arena, in-character behavior, no plot armor, both sides at standard prep. If the user specified a venue or condition ("underwater", "no prep", "with the Infinity Gauntlet"), honor it and call out how it shifts the matchup.
4. **Tell the story of the fight.** This is the part that's *not* templated. Length, shape, and structure follow the matchup — not a fixed Round 1 / Round 2 / Round 3 mold. A speed mismatch might be one paragraph. A real chess-match (Batman vs Iron Man) might be three. A team brawl might be a few short beats per pairing. Whatever shape serves the fight:
   - Open by naming the *real* axis of the fight — the one ability or trade-off that's actually going to decide it.
   - Show one or two memorable moments. Specific powers and specific gear, not generic "they trade blows."
   - Land the ending. Who wins, how, and — critically — *why the other one couldn't close it*.
5. **Close with a hook.** One short line: a "what if" flip (e.g., "with prep time, this swings the other way"), or a real-comics fun fact ("they actually teamed up in *JLA/Avengers* #3"), or both. One line, not a section.

**Hard rules for Mode B:**

- In-character only. No "Batman with prep beats anyone" memes unless the user opts into prep-time mode explicitly.
- No killing-blow language. Knocked out, webbed up, dragged off to the Phantom Zone — yes. Killed, gutted, dismembered — no.
- ≥3 combatants → bracket form (semis → final). Same narrative freedom; just more pairings.
- Cite the canonical pages you used (in a `**Sources:**` line at the bottom). If you used SuperHero DB stats to ground the matchup, cite that too.

**What this is *not*:**

- Not a fixed Round 1 / Round 2 / Round 3 template.
- Not a scorecard with a points total.
- Not a section-by-section form ("Verdict:", "How:", "Why not the other one:", "What if:"). Those are useful as *things to make sure you cover*, but they belong in the prose, not as headers.

## Examples

### Mode A example — "tell me about Miles Morales"

```markdown
# Miles Morales (Marvel)

**Real name:** Miles Gonzalo Morales
**First appeared:** *Ultimate Fallout* #4 (2011)
**Team:** Champions, Spider-Family (solo most of the time)

**Powers:**
- Wall-crawling and super-agility, like Peter Parker
- "Spider-sense" — a feeling that warns him about danger
- **Venom Blast** — a burst of bio-electricity from his hands that can short out a robot or knock out a bad guy
- **Camouflage** — he can turn invisible for short stretches
- Super strength and speed

**Gear:** Web-shooters (same design as Peter's), black-and-red Spidey suit

**Famous for:** He took up the Spider-Man name after the Ultimate version of Peter Parker died, then jumped to the main Marvel universe in *Secret Wars* (2015). The animated movie *Into the Spider-Verse* is based on his story.

**Friends:** Peter Parker, Ganke Lee (his best friend), Spider-Gwen
**Enemies:** The Prowler (who turns out to be his uncle Aaron), Tombstone

**Stats** (from SuperHero DB, divided by 10):
| Intelligence | Strength | Speed | Durability | Power | Combat |
|---|---|---|---|---|---|
| 7 | 4 | 3 | 6 | 7 | 5 |

**Sources:** [Wikipedia](https://en.wikipedia.org/wiki/Miles_Morales), [SuperHero DB](https://www.superherodb.com/spider-man-miles-morales/10-784/)
```

### Mode B example — "who would win, Thor vs Superman?"

(After running Mode A on both internally.)

```markdown
# Thor vs Superman

The real question in this fight isn't strength — they're both world-class there. It's *energy*. Thor channels lightning and weather; Superman is solar-powered and vulnerable to one very specific thing (magic). And Thor's hammer Mjolnir is *magic*. That's the axis.

They start with the obvious — a flying tackle at terminal velocity that flattens a mountainside. Superman is faster in a straight line, and he gets the better of the first exchange, but every time he closes, Thor catches him with Mjolnir, and the hammer hurts him in a way ordinary blows don't. Superman starts pulling his punches less. He tries the heat vision; Thor answers with a full-on lightning storm — not at Superman, but *around* him, soaking the air with charge until the heat vision fizzles.

Thor wins, just barely. The decisive move is Mjolnir to the jaw — the only blow in this fight that bypasses Superman's invulnerability. Superman couldn't close it because his usual finisher (overwhelming speed + strength) doesn't matter when the other guy's weapon ignores the durability gap entirely.

*With prep time, this swings the other way — Superman with a lead-lined Kryptonian artifact or a chunk of magic gear from the Fortress is a different fight. They actually teamed up against Thanos and the Anti-Monitor in* JLA/Avengers *#3.*

**Sources:** [Wikipedia — Thor](https://en.wikipedia.org/wiki/Thor_(Marvel_Comics)), [Wikipedia — Superman](https://en.wikipedia.org/wiki/Superman), [SuperHero DB — Thor](https://www.superherodb.com/thor/10-156/), [SuperHero DB — Superman](https://www.superherodb.com/superman/10-791/)
```

Note the shape: three paragraphs, no headers inside the body, no scorecard, no Round-1/Round-2 labels. The structure follows the fight.

## Common mistakes

- **Fabricating canonical facts** — first-appearance dates, real names, team affiliations. If you can't confirm against the APIs, mark `(unconfirmed)`.
- **Using `WebFetch` on marvel.com / dc.com / fandom / superherodb.com** — those user-agents are 403'd. This skill uses `curl + jq` against the Wikipedia and SuperHero JSON APIs instead. If you find yourself reaching for `WebFetch`, stop — go back to the Fetcher policy section.
- **Trusting SuperHero API's `biography.publisher` field as canonical** — it's frequently mislabeled (Deadpool's reads "Evil Deadpool"). Infer the publisher from the Wikipedia summary instead.
- **Cross-referencing only on name** when SuperHero API returns multiple hits — also check `biography.full-name` against Wikipedia. "Spider-Man" returns Peter Parker *and* Miles Morales *and* clones; the full-name field disambiguates them.
- **Letting Reddit/YouTube power-scaling threads creep in** — those aren't sources. SuperHero DB's rubric is the tiebreaker.
- **Adult-tone violence drift** — gore, killing blows, mature-rated arcs. If the prose starts sounding like a Punisher MAX comic, pull back to "knocked out / webbed up / sent home."
- **Skipping the Mode A pass before Mode B** — you can't write the fight without knowing the powers. Look up first.
- **Templating Mode B** — no Round 1 / Round 2 / Round 3, no scorecard headers, no "Verdict:" / "How:" / "Why not the other one:" labels. Tell the story; structure follows the fight.
- **Pretending verdicts are *correct*** — Marvel-vs-DC matchups are unresolvable. The skill aims for *consistent* (the same axis decides every time you set up the same fight), not *true*. Use the "what if" hook to acknowledge close calls without re-arguing the verdict.
- **Burying the citations** — every Mode A card and every Mode B write-up ends with a `**Sources:**` line listing the canonical pages used.
