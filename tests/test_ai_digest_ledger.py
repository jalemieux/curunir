# tests/test_ai_digest_ledger.py
"""Smoke tests for the ai-digest skill's on-disk artifacts.

The skill at skills/ai-digest/SKILL.md depends on two structural artifacts:
the bundled SKILL.md itself and the seeded URL ledger in
context.default/memory/ai-digest-sent.md. These tests guard the assumptions
the skill prose makes about those files (frontmatter, header line, append
format that grep -F can hit on the URL).
"""

import re
import shutil
import subprocess
from pathlib import Path

from src.skills import load_registry, parse_frontmatter

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPO_ROOT / "skills" / "ai-digest" / "SKILL.md"
LEDGER_SEED = REPO_ROOT / "context.default" / "memory" / "ai-digest-sent.md"


def test_skill_is_discoverable():
    registry = load_registry([REPO_ROOT / "skills"])
    assert "ai-digest" in registry
    assert registry["ai-digest"].path == SKILL_PATH


def test_skill_frontmatter_has_required_fields():
    fm = parse_frontmatter(SKILL_PATH.read_text())
    assert fm.get("name") == "ai-digest"
    assert fm.get("description")


def test_skill_documents_freshness_filter():
    body = SKILL_PATH.read_text()
    assert "freshness=pd" in body, "skill must use Brave freshness=pd as API-level filter"
    assert "verification ledger" in body.lower()
    assert "ai-digest-sent.md" in body, "skill must reference the dedup ledger by name"


def test_ledger_seed_has_header():
    text = LEDGER_SEED.read_text()
    first_line = text.splitlines()[0]
    assert first_line.startswith("#"), "seed file must start with a markdown header"
    assert "ledger" in first_line.lower()


def test_ledger_append_format_is_grep_friendly(tmp_path):
    """The skill instructs the agent to append `<DATE> <URL>` lines and to
    dedup by `grep -F <url>`. Confirm a sample append round-trips through
    grep -F and that the date prefix is parseable as ISO."""
    ledger = tmp_path / "ai-digest-sent.md"
    shutil.copy2(LEDGER_SEED, ledger)

    sample_url = "https://example.com/article-abc"
    sample_date = "2026-05-05"
    with ledger.open("a") as f:
        f.write(f"{sample_date} {sample_url}\n")

    result = subprocess.run(
        ["grep", "-F", sample_url, str(ledger)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert sample_url in result.stdout
    matched_date = result.stdout.split()[0]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", matched_date)
    assert matched_date == sample_date


def test_default_schedule_entry_is_disabled():
    """The bootstrap-seeded schedules.json ships an ai-digest entry that must
    be disabled by default — turning it on requires the user to confirm their
    BRAVE_API_KEY and review the skill's behavior first."""
    import json

    schedules = json.loads((REPO_ROOT / "context.default" / "schedules.json").read_text())
    digest_entries = [t for t in schedules if t.get("id") == "ai-digest-daily"]
    assert len(digest_entries) == 1, "exactly one ai-digest-daily entry expected"
    entry = digest_entries[0]
    assert entry["skill"] == "ai-digest"
    assert entry["enabled"] is False
    assert "cron" in entry and entry["cron"]
