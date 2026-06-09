# tests/test_digest_ledger.py
"""Smoke tests for the digest skill's on-disk artifacts.

The skill at skills/digest/SKILL.md depends on the bundled SKILL.md itself.
These tests guard the assumptions the skill prose makes about that file
(frontmatter, Inputs contract, topic-scoped ledger naming).
"""

import re
from pathlib import Path

from src.skills import load_registry, parse_frontmatter

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPO_ROOT / "skills" / "digest" / "SKILL.md"


def test_skill_is_discoverable():
    registry = load_registry([REPO_ROOT / "skills"])
    assert "digest" in registry
    assert registry["digest"].path == SKILL_PATH


def test_ai_digest_is_retired():
    """The topic-specific ai-digest skill is retired in favour of digest."""
    registry = load_registry([REPO_ROOT / "skills"])
    assert "ai-digest" not in registry
    assert not (REPO_ROOT / "skills" / "ai-digest").exists()


def test_skill_frontmatter_has_required_fields():
    fm = parse_frontmatter(SKILL_PATH.read_text())
    assert fm.get("name") == "digest"
    assert fm.get("description")


def test_skill_documents_freshness_filter():
    body = SKILL_PATH.read_text()
    assert "freshness=pd" in body, "skill must use Brave freshness=pd as API-level filter"
    assert "verification ledger" in body.lower()


def test_skill_ledger_is_topic_scoped():
    """The skill must reference a per-topic ledger path, not a hard-coded one."""
    body = SKILL_PATH.read_text()
    assert re.search(r"digest-.+-sent\.md", body), (
        "skill must reference a per-topic digest-<slug>-sent.md ledger"
    )
    # The retired ai-digest-sent.md may appear only in the migration note,
    # never as a step that reads or appends the ledger.
    for line in body.splitlines():
        if "ai-digest-sent.md" in line:
            assert "grep" not in line and ">>" not in line, (
                "skill must not read/append the retired ai-digest-sent.md ledger"
            )


def test_skill_has_inputs_contract_with_topic():
    """The skill takes its subject via a documented Inputs section."""
    body = SKILL_PATH.read_text()
    assert "## Inputs" in body, "skill must have an Inputs section"
    inputs_section = body.split("## Inputs", 1)[1]
    assert "Topic" in inputs_section, "Inputs section must name the Topic input"


def test_default_schedule_entry_is_disabled():
    """The bootstrap-seeded schedules.json ships a digest entry that must
    be disabled by default — turning it on requires the user to confirm their
    BRAVE_API_KEY and review the skill's behavior first."""
    import json

    schedules = json.loads((REPO_ROOT / "context.default" / "schedules.json").read_text())
    digest_entries = [t for t in schedules if t.get("id") == "ai-digest-daily"]
    assert len(digest_entries) == 1, "exactly one ai-digest-daily entry expected"
    entry = digest_entries[0]
    assert entry["skill"] == "digest"
    assert entry["enabled"] is False
    assert "cron" in entry and entry["cron"]
