# tests/test_idea_log_convention.py
"""Regression anchors for the idea-log memory convention (#506).

The idea log is a prompt-taught convention, not code: the finance persona
teaches capture / format / funnel / aging, and the dreaming skill both
freezes entry bodies and owns the one sanctioned eviction move (verbatim
Dormant-archival). These tests pin the load-bearing tokens so a prompt
rewrite can't silently drop the convention; the W4 eval task guards the
runtime behavior.
"""
from pathlib import Path

FINANCE_DOMAIN = Path("personas/finance/prompts/10-domain.md")
DREAMING_SKILL = Path("skills/dreaming/SKILL.md")


def test_finance_domain_teaches_idea_log():
    text = FINANCE_DOMAIN.read_text()
    assert "idea-log.md" in text
    # The five funnel statuses.
    for status in ("Spark", "Monitoring", "Graduated", "Abandoned", "Dormant"):
        assert status in text, status
    # The aging fields that make eviction computable.
    assert "Last touched" in text
    assert "90 days" in text
    assert "archives/idea-log-archive.md" in text


def test_dreaming_freezes_idea_log_with_archival_carveout():
    text = DREAMING_SKILL.read_text()
    # idea-log.md must sit in the frozen-content column...
    assert "idea-log.md" in text
    # ...with the one sanctioned exception spelled out: verbatim archival of
    # stale entries into the archive file, status -> Dormant.
    assert "idea-log-archive.md" in text
    assert "Dormant" in text
    assert "verbatim" in text.lower()
