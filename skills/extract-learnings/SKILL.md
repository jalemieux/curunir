---
name: extract-learnings
description: Use when processing Slack catch-ups, meeting notes, or incident summaries to extract durable knowledge worth remembering long-term while filtering out ephemeral items
---

# Extract Learnings

Extract stable, reusable knowledge from transient communications.

## What to Extract

Read `context/memory/README.md` for the current memory taxonomy — it defines which categories exist and what belongs in each. Place extracted facts in the appropriate category file.

## Quick Filter

Ask: "Will this still be true in 6 months?"

- **Yes** → Extract it
- **No/Maybe** → Skip it

## Output Format

For each extracted fact:

```markdown
## [Topic]
**Source:** [channel/meeting/doc] - [date]
**Fact:** [concise statement]
**Context:** [why this matters, if not obvious]
```

## Memory Integration

Memory writes are **upsert by heading**: the writer uses the first H2 (`## …`) of the extracted content as a stable section key. If a section with that exact heading already exists in the target file, it is replaced in place; otherwise the new section is appended.

To make this work:

1. Pick a canonical, stable topic heading the first time you record a fact.
2. When updating or correcting a previously-stored fact, **reuse the existing heading verbatim** so the writer can replace the section instead of accumulating duplicates.
3. Only invent a new heading for a genuinely new topic.
4. Link from MEMORY.md if the topic is new.

## Example

**Input:**
> QA: Why can't we use dots in Atlas keys?
> Answer: MongoDB limitation - dot notation for nested field access

**Output:**
```markdown
## MongoDB Key Constraints
**Source:** #falco-atlas - 2026-03-12
**Fact:** Dots prohibited in Atlas keys - MongoDB uses dot notation for nested field access (parent.child), creating query ambiguity
```
