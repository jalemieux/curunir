"""Phase 2b of agents and containers: one user profile per container.

``<shared>/profile.md`` is the only copy. The extraction loop writes profile
facts to it from any agent's conversation, the default agent may write/edit
it, a sibling agent may not, and a legacy ``memory/profile.md`` is moved
there once at boot.
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from onboarding.bootstrap import bootstrap_context, bootstrap_shared
from src.config import AgentConfig
from src.llm import LLMResponse
from src.memory_extractor import _collect_existing_headings, extract_learnings
from src.tools.fs_tools import exec_edit, exec_write


@pytest.fixture
def container(tmp_path):
    """A two-agent container: default 'everyday' and sibling 'finance'."""
    root = tmp_path / "context"
    cfgs = {}
    for name, default in (("everyday", True), ("finance", False)):
        ctx = root / "agents" / name
        (ctx / "memory").mkdir(parents=True)
        (ctx / "memory" / "README.md").write_text("# Memory\n")
        cfgs[name] = AgentConfig.for_agent(
            name, ctx, root, is_default=default,
            default_agent_name="everyday", skill_dirs=[tmp_path / "no-skills"],
        )
    return root, cfgs


def _history():
    return [
        {"role": "user", "content": "I moved to Lisbon."},
        {"role": "assistant", "content": "Noted."},
        {"role": "user", "content": "Also I prefer short answers."},
        {"role": "assistant", "content": "Got it."},
    ]


def _extraction(facts):
    return LLMResponse(
        text=json.dumps({
            "facts": facts,
            "summary": {"topic_slug": "move", "content": "The user moved."},
        }),
        tool_calls=None,
    )


# --- extraction ---------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["everyday", "finance"])
async def test_profile_fact_from_either_agent_lands_in_shared_profile(container, agent):
    root, cfgs = container
    cfg = cfgs[agent]
    facts = [
        {"file": "profile.md", "content": "## Home\n\n**Fact:** Lisbon\n"},
        {"file": "preferences.md", "content": "## Length\n\n**Fact:** short\n"},
    ]
    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock,
               return_value=_extraction(facts)):
        await extract_learnings(cfg, _history())

    assert "Lisbon" in (root / "profile.md").read_text()
    # Every other file still goes to the agent's own memory.
    assert "short" in (cfg.context_dir / "memory" / "preferences.md").read_text()
    for c in cfgs.values():
        assert not (c.context_dir / "memory" / "profile.md").exists()


@pytest.mark.asyncio
async def test_profile_fact_upserts_by_heading_in_shared_profile(container):
    root, cfgs = container
    (root / "profile.md").write_text(
        "# Owner Profile\n\n## Home\n\n**Fact:** Paris\n\n## Name\n\n**Fact:** Ana\n"
    )
    facts = [{"file": "./profile.md", "content": "## Home\n\n**Fact:** Lisbon\n"}]
    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock,
               return_value=_extraction(facts)):
        await extract_learnings(cfgs["finance"], _history())

    text = (root / "profile.md").read_text()
    assert "Lisbon" in text and "Paris" not in text
    assert "**Fact:** Ana" in text


@pytest.mark.asyncio
async def test_other_escapes_are_still_rejected(container):
    root, cfgs = container
    facts = [{"file": "../identity.md", "content": "## X\n\nnope\n"},
             {"file": "../../profile.md", "content": "## X\n\nnope\n"}]
    with patch("src.memory_extractor.call_llm", new_callable=AsyncMock,
               return_value=_extraction(facts)):
        await extract_learnings(cfgs["finance"], _history())

    assert not (cfgs["finance"].context_dir / "identity.md").exists()
    assert not (root / "agents" / "profile.md").exists()
    assert not (root / "profile.md").exists()


def test_existing_topics_list_the_shared_profile(container):
    root, cfgs = container
    (root / "profile.md").write_text("# Owner Profile\n\n## Name\n\nAna\n")
    mem = cfgs["finance"].context_dir / "memory"
    (mem / "profile.md").write_text("## Stale\n")

    headings = _collect_existing_headings(mem, profile_path=cfgs["finance"].profile_file)

    assert headings["profile.md"] == ["Name"]


# --- write gate ---------------------------------------------------------------

def test_default_agent_may_write_and_edit_shared_profile(container):
    root, cfgs = container
    cfg = cfgs["everyday"]
    path = str(root / "profile.md")

    assert exec_write({"file_path": path, "content": "## Name\n\nAna\n"}, cfg).startswith("Wrote")
    out = exec_edit({"file_path": path, "old_string": "Ana", "new_string": "Ana B."}, cfg)
    assert out.startswith("Replaced")
    assert "Ana B." in (root / "profile.md").read_text()


def test_sibling_agent_write_and_edit_are_refused(container):
    root, cfgs = container
    cfg = cfgs["finance"]
    profile = root / "profile.md"
    profile.write_text("## Name\n\nAna\n")

    out = exec_write({"file_path": str(profile), "content": "clobbered"}, cfg)
    assert out.startswith("Error:") and "'everyday'" in out
    out = exec_edit({"file_path": str(profile), "old_string": "Ana", "new_string": "Bo"}, cfg)
    assert out.startswith("Error:") and "'everyday'" in out
    assert profile.read_text() == "## Name\n\nAna\n"


def test_gate_sees_through_relative_paths_and_symlinks(container, tmp_path):
    root, cfgs = container
    cfg = cfgs["finance"]
    (root / "profile.md").write_text("x")
    link = tmp_path / "alias.md"
    link.symlink_to(root / "profile.md")
    dotted = cfg.context_dir / ".." / ".." / "profile.md"

    for path in (link, dotted):
        assert exec_write({"file_path": str(path), "content": "y"}, cfg).startswith("Error:")
    assert (root / "profile.md").read_text() == "x"


def test_sibling_may_still_write_its_own_memory(container):
    _, cfgs = container
    cfg = cfgs["finance"]
    target = cfg.context_dir / "memory" / "profile-notes.md"
    assert exec_write({"file_path": str(target), "content": "ok"}, cfg).startswith("Wrote")


# --- one-time move ------------------------------------------------------------

@pytest.fixture
def no_template(tmp_path, monkeypatch):
    monkeypatch.setattr("onboarding.bootstrap.DEFAULT_DIR", tmp_path / "no-defaults")


def test_legacy_profile_is_moved_to_shared(tmp_path, no_template):
    ctx = tmp_path / "context"
    (ctx / "memory").mkdir(parents=True)
    (ctx / "memory" / "profile.md").write_text("# Owner Profile\nAna")

    bootstrap_shared(ctx, [ctx])

    assert (ctx / "profile.md").read_text() == "# Owner Profile\nAna"
    assert not (ctx / "memory" / "profile.md").exists()


def test_existing_shared_profile_is_never_overwritten(tmp_path, no_template):
    ctx = tmp_path / "context"
    (ctx / "memory").mkdir(parents=True)
    (ctx / "profile.md").write_text("shared")
    (ctx / "memory" / "profile.md").write_text("legacy")

    bootstrap_shared(ctx, [ctx])

    assert (ctx / "profile.md").read_text() == "shared"
    # The losing copy is parked, not deleted, and no longer a memory .md file.
    assert not (ctx / "memory" / "profile.md").exists()
    assert (ctx / "memory" / "profile.md.legacy").read_text() == "legacy"


def test_move_happens_once(tmp_path, no_template):
    ctx = tmp_path / "context"
    (ctx / "memory").mkdir(parents=True)
    (ctx / "memory" / "profile.md").write_text("first")
    bootstrap_shared(ctx, [ctx])
    (ctx / "profile.md").write_text("edited since")

    bootstrap_shared(ctx, [ctx])

    assert (ctx / "profile.md").read_text() == "edited since"


def test_default_agent_profile_wins_and_siblings_are_parked(tmp_path, no_template):
    root = tmp_path / "context"
    default = root  # legacy agent, context: .
    sibling = root / "agents" / "finance"
    for ctx, text in ((default, "default's"), (sibling, "sibling's")):
        (ctx / "memory").mkdir(parents=True, exist_ok=True)
        (ctx / "memory" / "profile.md").write_text(text)

    bootstrap_shared(root, [default, sibling])

    assert (root / "profile.md").read_text() == "default's"
    assert not (sibling / "memory" / "profile.md").exists()
    assert (sibling / "memory" / "profile.md.legacy").read_text() == "sibling's"


def test_seeds_shared_profile_from_template_not_into_agents(tmp_path, monkeypatch):
    defaults = tmp_path / "context.default"
    (defaults / "memory").mkdir(parents=True)
    (defaults / "profile.md").write_text("# Owner Profile\n")
    (defaults / "memory" / "README.md").write_text("# Memory\n")
    monkeypatch.setattr("onboarding.bootstrap.DEFAULT_DIR", defaults)
    root = tmp_path / "context"
    sibling = root / "agents" / "finance"

    for ctx in (root, sibling):
        bootstrap_context(ctx)
    bootstrap_shared(root, [root, sibling])

    assert (root / "profile.md").read_text() == "# Owner Profile\n"
    assert not (sibling / "profile.md").exists()
    for ctx in (root, sibling):
        assert (ctx / "memory" / "README.md").exists()
        assert not (ctx / "memory" / "profile.md").exists()


def test_repo_template_lives_at_container_level():
    repo = Path(__file__).resolve().parents[1]
    assert (repo / "context.default" / "profile.md").is_file()
    assert not (repo / "context.default" / "memory" / "profile.md").exists()


# --- single-agent deployment ---------------------------------------------------

def test_legacy_layout_profile_is_context_profile():
    cfg = AgentConfig()
    assert cfg.profile_file == Path("./context/profile.md")
    assert cfg.is_default
    assert cfg.shared_state_files == (cfg.profile_file,)


def test_legacy_single_agent_may_write_its_profile(tmp_path):
    cfg = AgentConfig(context_dir=tmp_path)
    out = exec_write({"file_path": str(tmp_path / "profile.md"), "content": "Ana"}, cfg)
    assert out.startswith("Wrote")
