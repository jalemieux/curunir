# Prompt-owned delivery; sub-agents are workers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make artifact delivery a property of the *main* agent, stated once in system-prompt assembly, so a `delegate`-spawned sub-agent is never told to deliver and never can (#411).

**Architecture:** Extract the attach/PDF delivery instruction into a single cross-persona snippet injected by `build_static_prompt`. Make that function sub-agent-aware: the main agent gets the delivery snippet; a sub-agent gets a short "you're a worker, return a digest" note instead. Strip every `attach` reference out of skills. Keep the existing `is_sub_agent` unlock backstop unchanged.

**Tech Stack:** Python 3.12, pytest / pytest-asyncio.

## Global Constraints

- The static system prompt must remain **byte-stable across calls within a session** — auto-cache providers hash the prefix. Snippets are module-level constants (no timestamps, no per-call data). (`src/agent/system_prompt.py`, `Agent.__init__`)
- `attach` is a **base tool** the main agent always has; it must **never** be declared in any skill's `tools:` frontmatter. Opt-in `tools:` is only for genuine opt-in tools (`portfolio`, `to_audio`).
- Sub-agents return **text digests only** — no `attach`, no `to_audio`, no egress. The orchestrator owns delivery and the fact-check gate.
- Do not change the capability backstop (`_SUBAGENT_BLOCKED_UNLOCKS` in `src/agent/agent.py`) or its tests — they stay green.
- Conventional commits; no `Co-Authored-By` trailers (per repo `CLAUDE.md`).

---

### Task 1: Sub-agent-aware prompt assembly

**Files:**
- Modify: `src/agent/system_prompt.py` (add two constants; add `is_sub_agent` param to `build_static_prompt`)
- Modify: `src/agent/agent.py:204-207` (pass `self.is_sub_agent` into `build_static_prompt`)
- Modify: `personas/default/prompts/behavior.md:32-37` (remove the relocated delivery lines)
- Test: `tests/test_agent.py` (new `TestDeliveryPrompt` class)

**Interfaces:**
- Produces: `build_static_prompt(config: AgentConfig, is_sub_agent: bool = False) -> str`
- Produces: module constants `_DELIVERY_SNIPPET: str`, `_SUBAGENT_WORKER_NOTE: str` in `system_prompt.py`
- Consumes: `Agent.is_sub_agent` (already set in `Agent.__init__` from PR #418)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_agent.py` (top of file already imports `Agent`, `AgentConfig`):

```python
class TestDeliveryPrompt:
    """Delivery is the main agent's job (#411). The instruction lives in one
    cross-persona snippet; sub-agents get a worker note instead."""

    _MARKER = "the deliverable is a file"  # sentinel from the delivery snippet

    def test_main_agent_prompt_has_delivery_snippet(self, agent_config):
        from src.agent.system_prompt import build_static_prompt
        prompt = build_static_prompt(agent_config, is_sub_agent=False)
        assert self._MARKER in prompt

    def test_sub_agent_prompt_omits_delivery_snippet(self, agent_config):
        from src.agent.system_prompt import build_static_prompt
        prompt = build_static_prompt(agent_config, is_sub_agent=True)
        assert self._MARKER not in prompt
        assert "background worker" in prompt.lower()

    def test_delivery_snippet_is_cross_persona(self, tmp_context, tmp_skills):
        """finance/marketing never had the snippet in their bundles — they
        must get it now via shared assembly."""
        from src.agent.system_prompt import build_static_prompt
        from src.config import AgentConfig
        for persona in ("default", "finance", "marketing"):
            config = AgentConfig(
                identity_file=tmp_context / "identity.md",
                context_dir=tmp_context,
                skill_dirs=[tmp_skills],
                persona=persona,
            )
            prompt = build_static_prompt(config, is_sub_agent=False)
            assert self._MARKER in prompt, f"{persona} missing delivery snippet"

    def test_agent_init_wires_sub_agent_flag(self, agent_config):
        from src.agent.agent import Agent
        main = Agent(agent_config)
        sub = Agent(agent_config, is_sub_agent=True)
        assert self._MARKER in main.static_prompt
        assert self._MARKER not in sub.static_prompt
        assert "background worker" in sub.static_prompt.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agent.py::TestDeliveryPrompt -v`
Expected: FAIL — `build_static_prompt()` got an unexpected keyword argument `is_sub_agent` / marker not present.

- [ ] **Step 3: Add the constants and `is_sub_agent` param**

In `src/agent/system_prompt.py`, add the constants just below the imports (after the `logger = ...` line):

```python
# The single, cross-persona delivery instruction. Injected for the MAIN agent
# only — a sub-agent returns a digest and never delivers (#411). Lives here, not
# in any persona bundle, so finance/marketing get it too.
_DELIVERY_SNIPPET = """## Delivering artifacts

When your output is a substantial document — a report, analysis, research summary, memo — the deliverable is a file, not just chat text. Write it and deliver it with the `attach` tool by default. Don't wait to be asked, and don't ask which format to use.

Write the document as Markdown, then convert it to **PDF** — PDF is the default attachment format. Markdown is for rendering inline in the chat and email UI; the downloadable artifact is the PDF.

Convert with `pandoc`, which is already installed in the environment: `pandoc file.md -o file.pdf`. Do not render via HTML, headless Chromium, or CSS. If pandoc fails, attach the `.md` as a fallback."""

# Appended for a `delegate`-spawned sub-agent instead of the delivery snippet.
# A sub-agent is a background worker: it returns text, the orchestrator delivers.
_SUBAGENT_WORKER_NOTE = """## You are a background worker

You were spawned to complete a single task and return its result. Return your result as a text digest — you cannot and must not deliver anything to the user (no attachments, no audio, no email). The agent that delegated to you owns delivery and the fact-check gate. Do not try to attach or send an artifact; just return the content."""
```

Change the signature and append logic. Replace:

```python
def build_static_prompt(config: AgentConfig) -> str:
```

with:

```python
def build_static_prompt(config: AgentConfig, is_sub_agent: bool = False) -> str:
```

Then, immediately before `return "\n\n".join(parts)`, add:

```python
    parts.append(_SUBAGENT_WORKER_NOTE if is_sub_agent else _DELIVERY_SNIPPET)
```

- [ ] **Step 4: Wire the flag through `Agent.__init__`**

In `src/agent/agent.py`, change the `build_static_prompt(config)` call (around line 205):

```python
        self.static_prompt = (
            build_static_prompt(config, is_sub_agent=is_sub_agent)
            + f"\n\nConversation started at: {self._boot_time.isoformat()}"
        )
```

Note: `is_sub_agent` is the constructor parameter (already present); `self.is_sub_agent` is assigned earlier in `__init__`. Use the local param `is_sub_agent` since it is in scope at this point.

- [ ] **Step 5: Remove the relocated lines from the default persona**

In `personas/default/prompts/behavior.md`, the `## Deliverables` section currently holds the attach/PDF lines now in the snippet. Delete the three relocated bullets (the "substantial document … attach", "Write the document as Markdown … PDF", and "Convert with `pandoc` …" bullets), leaving the `## Deliverables` heading with only the runtime-install rule:

```markdown
## Deliverables

- Never `pip install` (or otherwise install packages at runtime) to produce a deliverable. The tooling you need — pandoc and LaTeX — is already in the image. The environment is ephemeral, so a runtime install is slow, non-deterministic, and gone next session.
```

(The `## Workspace` section below it is unchanged — sub-agents still write files.)

- [ ] **Step 6: Run the new tests + the prompt-stability regression**

Run: `pytest tests/test_agent.py::TestDeliveryPrompt tests/test_agent.py::TestSystemPromptCaching -v`
Expected: PASS (new delivery tests pass; cache-stability tests still pass).

- [ ] **Step 7: Commit**

```bash
git add src/agent/system_prompt.py src/agent/agent.py personas/default/prompts/behavior.md tests/test_agent.py
git commit -m "feat(agent): own artifact delivery in the system prompt; sub-agents get a worker note (#411)"
```

---

### Task 2: Remove `tools: attach` frontmatter from all skills

**Files:**
- Modify: `skills/financial-analysis/SKILL.md:6`
- Modify: `skills/investment-memo/SKILL.md:6`
- Modify: `skills/catalyst-memo/SKILL.md:7`
- Modify: `skills/deep-research/SKILL.md:7`
- Modify: `skills/deep-research-guided/SKILL.md:6`
- Modify: `skills/superheroes/SKILL.md:6`
- Modify: `skills/skill-factory/SKILL.md:66`
- Test: `tests/test_skills.py` (new grep-guard test)

**Interfaces:**
- Consumes: nothing. Produces: an invariant ("no SKILL.md declares `tools: attach`") that the guard test enforces.

- [ ] **Step 1: Write the failing guard test**

Add to `tests/test_skills.py` (create the test class if the file lacks one):

```python
import re
from pathlib import Path


def test_no_skill_declares_attach_tool():
    """`attach` is a base tool, not opt-in. A skill declaring it is the #411
    leak vector — delivery is the system prompt's job."""
    skills_root = Path(__file__).resolve().parent.parent / "skills"
    offenders = []
    for md in skills_root.glob("**/SKILL.md"):
        for line in md.read_text().splitlines():
            if re.match(r"^\s*tools:", line) and "attach" in line:
                offenders.append(str(md.relative_to(skills_root)))
    assert not offenders, f"skills must not declare `tools: attach`: {offenders}"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_skills.py::test_no_skill_declares_attach_tool -v`
Expected: FAIL — offenders list contains financial-analysis, investment-memo, catalyst-memo, deep-research, deep-research-guided, superheroes, skill-factory.

- [ ] **Step 3: Delete the `tools: attach` line from each of the 7 SKILL.md files**

In each file, remove the single frontmatter line `tools: attach` (leave the rest of the frontmatter intact). For example in `skills/superheroes/SKILL.md`:

```diff
 description: "..."
-tools: attach
 ---
```

Do the same in the other six files listed above.

- [ ] **Step 4: Run the guard test to verify it passes**

Run: `pytest tests/test_skills.py::test_no_skill_declares_attach_tool -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/*/SKILL.md tests/test_skills.py
git commit -m "refactor(skills): drop redundant `tools: attach` frontmatter (#411)"
```

---

### Task 3: Strip attach prose from the research skills

**Files:**
- Modify: `skills/financial-analysis/SKILL.md` (Step 5 + the sub-agent block at lines ~14-19)
- Modify: `skills/deep-research/SKILL.md` (deliver step ~171 + mistake bullet ~234)
- Modify: `skills/deep-research-guided/SKILL.md` (deliver step ~210 + mistake bullet ~302)

**Interfaces:** Consumes nothing; produces no symbols. Prose-only.

- [ ] **Step 1: financial-analysis — delete the sub-agent block**

The prompt's worker note now covers this generically. Remove lines 14-19 (the blockquote that begins `> **When running as a sub-agent (under `delegate`):**` through `to you in that context regardless.`), including the surrounding blank line so two blank lines don't remain.

- [ ] **Step 2: financial-analysis — simplify Step 5 (Deliver)**

Replace the current Step 5 body:

```markdown
1. Write the markdown to `context/workspace/generated/<TICKER>-<YYYY-MM-DD>.md`.
2. Convert to PDF with pandoc:

   ```bash
   pandoc context/workspace/generated/<TICKER>-<DATE>.md \
     -o context/workspace/generated/<TICKER>-<DATE>.pdf
   ```
3. Attach the PDF: `attach(path="context/workspace/generated/<TICKER>-<DATE>.pdf")`.
   If pandoc fails, attach the `.md` as fallback.
4. In your reply, post the **Bottom Line** section verbatim plus the
   scenario table inline. The full report is the attachment.
```

with:

```markdown
1. Write the markdown to `context/workspace/generated/<TICKER>-<YYYY-MM-DD>.md`.
   Delivery (PDF conversion + attachment) is handled automatically — you do not
   call `attach`.
2. In your reply, post the **Bottom Line** section verbatim plus the
   scenario table inline. The full report is delivered as the attached file.
```

- [ ] **Step 3: financial-analysis — drop the "Forgetting attach()" mistake**

In the "Common mistakes" list, delete the bullet:

```markdown
- **Forgetting `attach()`** — the PDF must be attached, not just written
  to disk.
```

- [ ] **Step 4: deep-research — remove the attach instruction**

Replace:

```markdown
Attach the **PDF**: `attach(path="{file}.pdf")`. If PDF conversion fails, attach the `.md` instead. Never convert to HTML or other formats.

Reply with a concise summary (key findings + bullets) as your text response. The full report is the attachment. If the fact-check surfaced material corrections, mention that in one line so the user knows the addendum is worth a glance.
```

with:

```markdown
Write the report as Markdown to the generated workspace path. PDF conversion and attachment are handled automatically — do not call `attach`. Never hand-render to HTML or other formats.

Reply with a concise summary (key findings + bullets) as your text response. The full report is delivered as the attached file. If the fact-check surfaced material corrections, mention that in one line so the user knows the addendum is worth a glance.
```

Then in the "Common mistakes" list, delete the bullet `- **Forgetting `attach()`** — the report file must be attached, not just written.` (Keep the "Attaching .md instead of .pdf" bullet — it is PDF-quality guidance, not an attach instruction.)

- [ ] **Step 5: deep-research-guided — same two edits as Step 4**

The deliver paragraph and the "Forgetting `attach()`" bullet are byte-identical to deep-research. Apply the same replacement (deliver paragraph) and the same bullet deletion.

- [ ] **Step 6: Verify no stray attach() call remains in these three files**

Run: `grep -rn "attach(" skills/financial-analysis/SKILL.md skills/deep-research/SKILL.md skills/deep-research-guided/SKILL.md`
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add skills/financial-analysis/SKILL.md skills/deep-research/SKILL.md skills/deep-research-guided/SKILL.md
git commit -m "docs(skills): research skills no longer call attach; delivery is prompt-owned (#411)"
```

---

### Task 4: Strip attach prose from the memo skills

**Files:**
- Modify: `skills/catalyst-memo/SKILL.md` (deliver block ~505-518, step 10 ~552, mistake ~611)
- Modify: `skills/investment-memo/SKILL.md` (deliver block ~385-390, mistake ~537)

**Interfaces:** Prose-only.

- [ ] **Step 1: catalyst-memo — remove the attach block**

Replace:

```markdown
Attach:

```
attach(path="context/workspace/generated/{slug}-{date}.pdf")
```

If pandoc fails, attach the `.md` as fallback.

In the text reply, post the **Executive Summary** verbatim plus one line on
whether the fact-check found material corrections. The full memo is the
attachment.
```

with:

```markdown
Write the memo Markdown to `context/workspace/generated/{slug}-{date}.md`; PDF
conversion and attachment are handled automatically — do not call `attach`.

In the text reply, post the **Executive Summary** verbatim plus one line on
whether the fact-check found material corrections. The full memo is delivered
as the attached file.
```

- [ ] **Step 2: catalyst-memo — fix the step-10 summary line**

Replace `10. **Deliver** — PDF via pandoc, attach, post Executive Summary in reply.`
with `10. **Deliver** — write the memo Markdown, post the Executive Summary in reply (PDF/attachment is automatic).`

- [ ] **Step 3: catalyst-memo — drop the "Forgetting attach()" mistake**

Delete:

```markdown
- **Forgetting `attach()`.** The PDF must be attached, not just written
  to disk.
```

(Keep the "Attaching .md instead of .pdf" bullet — PDF-quality guidance.)

- [ ] **Step 4: investment-memo — remove the attach instruction**

Replace:

```markdown
Attach the PDF: `attach(path="context/workspace/generated/{slug}-{date}.pdf")`. If
pandoc fails, attach the `.md` as fallback.

In the text reply, post the **Executive Summary** verbatim plus one line on
whether the fact-check found material corrections. The full memo is the
attachment.
```

with:

```markdown
Write the memo Markdown to the generated workspace path; PDF conversion and
attachment are handled automatically — do not call `attach`.

In the text reply, post the **Executive Summary** verbatim plus one line on
whether the fact-check found material corrections. The full memo is delivered
as the attached file.
```

- [ ] **Step 5: investment-memo — drop the "Forgetting attach()" mistake**

Delete:

```markdown
- **Forgetting `attach()`.** The PDF must be attached, not just written
  to disk.
```

- [ ] **Step 6: Verify no stray attach() call remains**

Run: `grep -rn "attach(" skills/catalyst-memo/SKILL.md skills/investment-memo/SKILL.md`
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add skills/catalyst-memo/SKILL.md skills/investment-memo/SKILL.md
git commit -m "docs(skills): memo skills no longer call attach; delivery is prompt-owned (#411)"
```

---

### Task 5: Update the skill-authoring docs

**Files:**
- Modify: `skills/skill-factory/SKILL.md` (lines ~42, ~64-72, ~106)
- Modify: `skills/skill-factory/references/template.md:14`

**Interfaces:** Prose-only. Teaches future authors that `attach` is base, not opt-in.

- [ ] **Step 1: skill-factory — fix the frontmatter table row**

Replace the `tools` row:

```markdown
| `tools`       | no       | comma-separated opt-in tools (currently only `attach`)        |
```

with:

```markdown
| `tools`       | no       | comma-separated opt-in tools (e.g. `portfolio`, `to_audio`). NOT `attach` — that is a base tool the main agent always has. |
```

- [ ] **Step 2: skill-factory — fix the example + explanation**

Replace the example frontmatter:

```markdown
name: my-skill
description: Use when ...
tools: attach
---
```

```
Currently only `attach` is available (delivers a file as an email
attachment / CLI file path). New opt-in tools are registered in
`src/tools/schemas.py` — see `src/tools/README.md` for the mechanics. Don't
```

with:

```markdown
name: my-skill
description: Use when ...
tools: portfolio
---
```

```
Opt-in tools available today are `portfolio` and `to_audio`. Do **not** declare
`attach`: it is a base tool the main agent always has, and a sub-agent must
never get it (delivery is the orchestrator's job, #411). New opt-in tools are
registered in `src/tools/schemas.py` — see `src/tools/README.md` for the
mechanics. Don't
```

- [ ] **Step 3: skill-factory — fix the "Declaring tools that aren't registered" mistake**

Replace:

```markdown
- **Declaring tools that aren't registered.** Only `attach` is currently
  available as opt-in.
```

with:

```markdown
- **Declaring tools that aren't registered.** Opt-in tools are `portfolio`
  and `to_audio`. `attach` is a base tool — never declare it.
```

- [ ] **Step 4: template.md — fix the commented example**

Replace `# tools: attach       # uncomment if you need an opt-in tool`
with `# tools: portfolio    # uncomment if you need an opt-in tool (NOT attach — that's a base tool)`

- [ ] **Step 5: Verify**

Run: `grep -rn "attach" skills/skill-factory/`
Expected: no remaining line that presents `attach` as an opt-in `tools:` value.

- [ ] **Step 6: Commit**

```bash
git add skills/skill-factory/SKILL.md skills/skill-factory/references/template.md
git commit -m "docs(skill-factory): attach is a base tool, not opt-in (#411)"
```

---

### Task 6: Update project docs

**Files:**
- Modify: `CLAUDE.md` (the "Sub-agents cannot unlock delivery tools" paragraph under Tools)
- Modify: `docs/architecture.md` (changelog entry; delivery-model note if a relevant section exists)

**Interfaces:** Docs-only.

- [ ] **Step 1: CLAUDE.md — reframe the delivery-boundary note**

Find the paragraph beginning **"Sub-agents cannot unlock delivery tools."** under the Tools section. Update it to describe the new two-layer model: delivery is instructed only in the main agent's system prompt (the shared `_DELIVERY_SNIPPET`); sub-agents get `_SUBAGENT_WORKER_NOTE` instead and skills no longer declare `attach`. Keep the sentence that the `_SUBAGENT_BLOCKED_UNLOCKS` denylist remains as the capability backstop. Replace the old "phase delegate loading `financial-analysis`, which declares `tools: attach`" example (no longer true) with: "skills no longer declare `attach` at all — delivery is owned by the main agent's prompt."

- [ ] **Step 2: architecture.md — add a changelog entry**

Append to the changelog at the bottom of `docs/architecture.md`:

```markdown
- **Prompt-owned delivery (#411):** the attach/PDF delivery instruction is a single cross-persona snippet injected by `build_static_prompt` for the main agent only; sub-agents receive a worker note instead and skills no longer declare or call `attach`. The `_SUBAGENT_BLOCKED_UNLOCKS` unlock denylist remains as a capability backstop.
```

- [ ] **Step 3: Run the full test suite**

Run: `pytest tests/ -q`
Expected: PASS (all ~200 tests, including the unchanged #411 backstop tests and the new delivery/guard tests).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/architecture.md
git commit -m "docs: document prompt-owned delivery model (#411)"
```

---

## Finish (after all tasks pass)

Per `superpowers:finishing-a-development-branch`:
1. Push the branch and open a **new PR** referencing #411.
2. **Close PR #418** (superseded), linking the new PR.
3. **Update issue #411** to point at the new PR.

## Self-Review

- **Spec coverage:** §1 shared snippet → Task 1 Step 3/5. §2 sub-agent-aware assembly → Task 1 Steps 3/4 + tests. §3 skills stop calling attach → Tasks 2/3/4/5. §4 backstop unchanged → Global Constraints + no task (existing tests stay green, asserted in Task 6 Step 3). Docs → Task 6. Finish steps (new PR / close #418 / update #411) → Finish section.
- **Placeholder scan:** none — all edits quote exact old/new text.
- **Type consistency:** `build_static_prompt(config, is_sub_agent=False)` and constants `_DELIVERY_SNIPPET` / `_SUBAGENT_WORKER_NOTE` used identically in `system_prompt.py`, `agent.py`, and tests. Sentinel marker `"the deliverable is a file"` appears verbatim in `_DELIVERY_SNIPPET` and in the tests.
