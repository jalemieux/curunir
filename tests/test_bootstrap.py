"""Tests for src/bootstrap.py — context directory bootstrapping."""

import pytest
from pathlib import Path
from onboarding.bootstrap import bootstrap_context


@pytest.fixture
def setup_dirs(tmp_path, monkeypatch):
    """Create a temporary context.default/ and context/ dir pair."""
    default_dir = tmp_path / "context.default"
    context_dir = tmp_path / "context"
    monkeypatch.setattr("onboarding.bootstrap.DEFAULT_DIR", default_dir)
    return default_dir, context_dir


def test_copies_missing_files(setup_dirs):
    default_dir, context_dir = setup_dirs
    default_dir.mkdir()
    (default_dir / "identity.md").write_text("hello")
    (default_dir / "schedules.json").write_text("[]")

    bootstrap_context(context_dir)

    assert (context_dir / "identity.md").read_text() == "hello"
    assert (context_dir / "schedules.json").read_text() == "[]"


def test_does_not_overwrite_existing(setup_dirs):
    default_dir, context_dir = setup_dirs
    default_dir.mkdir()
    (default_dir / "identity.md").write_text("default")

    context_dir.mkdir()
    (context_dir / "identity.md").write_text("customized")

    bootstrap_context(context_dir)

    assert (context_dir / "identity.md").read_text() == "customized"


def test_copies_nested_directories(setup_dirs):
    default_dir, context_dir = setup_dirs
    default_dir.mkdir()
    (default_dir / "memory").mkdir()
    (default_dir / "memory" / "README.md").write_text("# Memory")

    bootstrap_context(context_dir)

    assert (context_dir / "memory" / "README.md").read_text() == "# Memory"


def test_no_default_dir_is_noop(setup_dirs):
    _, context_dir = setup_dirs
    # default_dir not created
    bootstrap_context(context_dir)
    assert not context_dir.exists()


def test_creates_context_dir_if_missing(setup_dirs):
    default_dir, context_dir = setup_dirs
    default_dir.mkdir()
    (default_dir / "identity.md").write_text("hello")

    assert not context_dir.exists()
    bootstrap_context(context_dir)
    assert context_dir.is_dir()
    assert (context_dir / "identity.md").exists()


from onboarding.bootstrap import bootstrap_persona


@pytest.fixture
def persona_dirs(tmp_path, monkeypatch):
    personas_dir = tmp_path / "personas"
    context_dir = tmp_path / "context"
    monkeypatch.setattr("onboarding.bootstrap.PERSONAS_DIR", personas_dir)
    return personas_dir, context_dir


def test_persona_copies_expertise(persona_dirs):
    personas_dir, context_dir = persona_dirs
    exp = personas_dir / "finance" / "expertise"
    exp.mkdir(parents=True)
    (exp / "10-domain.md").write_text("DOMAIN")
    bootstrap_persona(context_dir, "finance")
    assert (context_dir / "persona" / "10-domain.md").read_text() == "DOMAIN"


def test_persona_does_not_overwrite(persona_dirs):
    personas_dir, context_dir = persona_dirs
    exp = personas_dir / "finance" / "expertise"
    exp.mkdir(parents=True)
    (exp / "10-domain.md").write_text("NEW")
    dest = context_dir / "persona"
    dest.mkdir(parents=True)
    (dest / "10-domain.md").write_text("EXISTING")
    bootstrap_persona(context_dir, "finance")
    assert (dest / "10-domain.md").read_text() == "EXISTING"


def test_persona_missing_expertise_is_noop(persona_dirs):
    personas_dir, context_dir = persona_dirs
    (personas_dir / "finance").mkdir(parents=True)
    bootstrap_persona(context_dir, "finance")
    assert not (context_dir / "persona").exists()
