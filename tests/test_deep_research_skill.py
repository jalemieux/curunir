"""Tests for the deep-research skill — verifies it delegates research to sub-agents."""
from pathlib import Path

import pytest

from src.agent.agent import _parse_skill_tools
from src.skills import parse_frontmatter

SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "deep-research" / "SKILL.md"


@pytest.fixture
def skill_text() -> str:
    return SKILL_PATH.read_text()


class TestDeepResearchFrontmatter:
    def test_skill_file_exists(self):
        assert SKILL_PATH.exists(), f"deep-research SKILL.md not found at {SKILL_PATH}"

    def test_frontmatter_declares_delegate(self, skill_text):
        tools = _parse_skill_tools(skill_text)
        assert "delegate" in tools, (
            f"deep-research must declare 'delegate' in its frontmatter tools list, got: {tools}"
        )

    def test_frontmatter_keeps_attach(self, skill_text):
        tools = _parse_skill_tools(skill_text)
        assert "attach" in tools, (
            f"deep-research must keep 'attach' in its frontmatter tools list, got: {tools}"
        )

    def test_frontmatter_name_and_description_present(self, skill_text):
        fm = parse_frontmatter(skill_text)
        assert fm.get("name") == "deep-research"
        assert fm.get("description"), "frontmatter description must not be empty"


class TestDeepResearchDelegationInstructions:
    def test_step_3_instructs_delegation(self, skill_text):
        body = _step_3_section(skill_text)
        assert "delegate(" in body, (
            "Step 3 must instruct the agent to call delegate(...) for each sub-question. "
            f"Step 3 body was:\n{body}"
        )

    def test_step_3_does_not_run_inline_searches(self, skill_text):
        """Step 3 should not instruct the agent to run searches/web_fetch inline."""
        body = _step_3_section(skill_text).lower()
        # Inline-research signals from the previous version
        forbidden = [
            "use `web_fetch`",
            "run 1-2 targeted searches",
        ]
        offenders = [phrase for phrase in forbidden if phrase in body]
        assert not offenders, (
            f"Step 3 still contains inline-research instructions: {offenders}. "
            f"Research must be delegated to sub-agents."
        )

    def test_examples_mention_delegation(self, skill_text):
        examples = _section(skill_text, "## Examples")
        assert "delegate" in examples.lower(), (
            "Examples section must show the delegation workflow. "
            f"Examples body was:\n{examples}"
        )

    def test_common_mistakes_warns_against_inline_research(self, skill_text):
        mistakes = _section(skill_text, "## Common Mistakes")
        assert "delegate" in mistakes.lower(), (
            "Common Mistakes should warn against doing research inline instead of delegating. "
            f"Body was:\n{mistakes}"
        )


def _section(text: str, heading: str) -> str:
    """Extract the body of a top-level heading section."""
    lines = text.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if line.startswith(heading):
            capturing = True
            continue
        if capturing and line.startswith("## ") and not line.startswith(heading):
            break
        if capturing:
            out.append(line)
    return "\n".join(out)


def _step_3_section(text: str) -> str:
    """Extract the body of the Step 3 section."""
    lines = text.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if line.startswith("### Step 3"):
            capturing = True
            continue
        if capturing and line.startswith("### "):
            break
        if capturing:
            out.append(line)
    return "\n".join(out)
