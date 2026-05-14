# tests/test_migrate_memory_layout.py
import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.config import AgentConfig
from src.llm import LLMResponse


def _load_module():
    """Import scripts/migrate_memory_layout.py without requiring it to be a package."""
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "scripts" / "migrate_memory_layout.py"
    spec = importlib.util.spec_from_file_location("migrate_memory_layout", script_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["migrate_memory_layout"] = module
    spec.loader.exec_module(module)
    return module


mm = _load_module()


def _classification(*entries: tuple[int, str]) -> LLMResponse:
    payload = [{"index": i, "class": cls} for i, cls in entries]
    return LLMResponse(text=json.dumps(payload), tool_calls=None)


def _seed_memory_dir(tmp_path: Path, body: str) -> Path:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "preferences.md").write_text(body)
    return memory_dir


SAMPLE_MIXED = (
    "# Owner Preferences\n\n"
    "Some preamble describing the file.\n\n"
    "## Name\n"
    "**Source:** chat - 2026-04-01\n"
    "**Fact:** The user is addressed as Professor.\n\n"
    "## Response Style\n"
    "**Source:** chat - 2026-04-02\n"
    "**Fact:** Detailed in substance, terse in manner.\n\n"
    "## Timezone\n"
    "**Source:** chat - 2026-04-03\n"
    "**Fact:** America/New_York.\n"
)


def test_split_sections_extracts_preamble_and_sections():
    preamble, sections = mm._split_sections(SAMPLE_MIXED)
    assert "Some preamble" in preamble
    assert preamble.startswith("# Owner Preferences")
    assert len(sections) == 3
    assert sections[0].heading == "## Name"
    assert sections[1].heading == "## Response Style"
    assert sections[2].heading == "## Timezone"
    # Raw slice must round-trip the source byte-for-byte
    assert preamble + "".join(s.raw for s in sections) == SAMPLE_MIXED


def test_split_sections_no_headings():
    preamble, sections = mm._split_sections("just some text\nno headings here\n")
    assert preamble == "just some text\nno headings here\n"
    assert sections == []


def test_parse_classification_happy_path():
    raw = json.dumps(
        [
            {"index": 0, "class": "profile"},
            {"index": 1, "class": "preferences"},
            {"index": 2, "class": "profile"},
        ]
    )
    parsed = mm._parse_classification(raw, 3)
    assert parsed == {0: "profile", 1: "preferences", 2: "profile"}


def test_parse_classification_strips_code_fences():
    raw = "```json\n" + json.dumps([{"index": 0, "class": "profile"}]) + "\n```"
    assert mm._parse_classification(raw, 1) == {0: "profile"}


def test_parse_classification_rejects_invalid_class():
    raw = json.dumps([{"index": 0, "class": "other"}])
    with pytest.raises(ValueError):
        mm._parse_classification(raw, 1)


def test_parse_classification_rejects_missing_indices():
    raw = json.dumps([{"index": 0, "class": "profile"}])
    with pytest.raises(ValueError, match="Missing classification"):
        mm._parse_classification(raw, 2)


def test_parse_classification_rejects_duplicate_indices():
    raw = json.dumps(
        [
            {"index": 0, "class": "profile"},
            {"index": 0, "class": "preferences"},
        ]
    )
    with pytest.raises(ValueError, match="Duplicate"):
        mm._parse_classification(raw, 1)


def test_parse_classification_rejects_out_of_range():
    raw = json.dumps([{"index": 5, "class": "profile"}])
    with pytest.raises(ValueError, match="out of range"):
        mm._parse_classification(raw, 2)


def test_migrate_dry_run_does_not_mutate(tmp_path):
    memory_dir = _seed_memory_dir(tmp_path, SAMPLE_MIXED)
    config = AgentConfig()
    classification = _classification(
        (0, "profile"), (1, "preferences"), (2, "profile")
    )

    with patch(
        "migrate_memory_layout.call_llm",
        new_callable=AsyncMock,
        return_value=classification,
    ):
        summary = asyncio.run(mm.migrate(memory_dir, config, dry_run=True))

    assert summary["status"] == "ok"
    assert summary["dry_run"] is True
    # No files written
    assert not (memory_dir / "profile.md").exists()
    # Original is intact
    assert (memory_dir / "preferences.md").read_text() == SAMPLE_MIXED
    # Dry-run text returned for review
    assert "## Name" in summary["profile_text"]
    assert "## Timezone" in summary["profile_text"]
    assert "## Response Style" in summary["preferences_text"]


def test_migrate_writes_files_and_backup(tmp_path):
    memory_dir = _seed_memory_dir(tmp_path, SAMPLE_MIXED)
    config = AgentConfig()
    classification = _classification(
        (0, "profile"), (1, "preferences"), (2, "profile")
    )

    with patch(
        "migrate_memory_layout.call_llm",
        new_callable=AsyncMock,
        return_value=classification,
    ):
        summary = asyncio.run(mm.migrate(memory_dir, config, dry_run=False))

    assert summary["status"] == "ok"
    profile = (memory_dir / "profile.md").read_text()
    prefs = (memory_dir / "preferences.md").read_text()

    assert "# Owner Profile" in profile
    assert "## Name" in profile
    assert "## Timezone" in profile
    assert "## Response Style" not in profile

    assert "# Owner Preferences" in prefs
    assert "## Response Style" in prefs
    assert "## Name" not in prefs
    assert "## Timezone" not in prefs

    # Preamble follows preferences.md (its historical home)
    assert "Some preamble" in prefs
    assert "Some preamble" not in profile

    # Backup file exists with the original content
    backup_path = Path(summary["backup_path"])
    assert backup_path.exists()
    assert backup_path.read_text() == SAMPLE_MIXED


def test_migrate_already_migrated_is_no_op(tmp_path):
    memory_dir = _seed_memory_dir(tmp_path, SAMPLE_MIXED)
    (memory_dir / "profile.md").write_text("# Owner Profile\n")
    config = AgentConfig()

    with patch(
        "migrate_memory_layout.call_llm", new_callable=AsyncMock
    ) as mock_llm:
        summary = asyncio.run(mm.migrate(memory_dir, config, dry_run=False))

    assert summary["status"] == "already-migrated"
    mock_llm.assert_not_called()
    # preferences.md untouched
    assert (memory_dir / "preferences.md").read_text() == SAMPLE_MIXED


def test_migrate_force_reruns(tmp_path):
    memory_dir = _seed_memory_dir(tmp_path, SAMPLE_MIXED)
    (memory_dir / "profile.md").write_text("# Old Profile\n")
    config = AgentConfig()
    classification = _classification(
        (0, "profile"), (1, "preferences"), (2, "profile")
    )

    with patch(
        "migrate_memory_layout.call_llm",
        new_callable=AsyncMock,
        return_value=classification,
    ):
        summary = asyncio.run(mm.migrate(memory_dir, config, force=True))

    assert summary["status"] == "ok"
    assert "# Owner Profile" in (memory_dir / "profile.md").read_text()


def test_migrate_missing_preferences_is_no_op(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    config = AgentConfig()

    summary = asyncio.run(mm.migrate(memory_dir, config))
    assert summary["status"] == "nothing-to-migrate"


def test_migrate_no_sections_warns(tmp_path):
    memory_dir = _seed_memory_dir(tmp_path, "just a bullet list\n- foo\n- bar\n")
    config = AgentConfig()

    summary = asyncio.run(mm.migrate(memory_dir, config))
    assert summary["status"] == "no-sections"
    # File untouched
    assert (memory_dir / "preferences.md").read_text() == (
        "just a bullet list\n- foo\n- bar\n"
    )


def test_migrate_missing_memory_dir(tmp_path):
    config = AgentConfig()
    with pytest.raises(FileNotFoundError):
        asyncio.run(mm.migrate(tmp_path / "nope", config))


def test_migrate_preserves_every_section_byte_for_byte(tmp_path):
    """The defensive no-loss check: every section's text lands in exactly one output."""
    memory_dir = _seed_memory_dir(tmp_path, SAMPLE_MIXED)
    config = AgentConfig()
    classification = _classification(
        (0, "profile"), (1, "preferences"), (2, "profile")
    )

    with patch(
        "migrate_memory_layout.call_llm",
        new_callable=AsyncMock,
        return_value=classification,
    ):
        asyncio.run(mm.migrate(memory_dir, config))

    profile = (memory_dir / "profile.md").read_text()
    prefs = (memory_dir / "preferences.md").read_text()

    # The original facts (stripped) must all appear in exactly one of the outputs.
    assert "The user is addressed as Professor." in profile
    assert "The user is addressed as Professor." not in prefs

    assert "Detailed in substance, terse in manner." in prefs
    assert "Detailed in substance, terse in manner." not in profile

    assert "America/New_York." in profile
    assert "America/New_York." not in prefs


def test_migrate_rejects_invalid_llm_response(tmp_path):
    memory_dir = _seed_memory_dir(tmp_path, SAMPLE_MIXED)
    config = AgentConfig()
    bad = LLMResponse(text="this is not json", tool_calls=None)

    with patch(
        "migrate_memory_layout.call_llm", new_callable=AsyncMock, return_value=bad
    ):
        with pytest.raises(ValueError):
            asyncio.run(mm.migrate(memory_dir, config))

    # Original is intact, no partial writes
    assert (memory_dir / "preferences.md").read_text() == SAMPLE_MIXED
    assert not (memory_dir / "profile.md").exists()
