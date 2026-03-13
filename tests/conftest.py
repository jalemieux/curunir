# tests/conftest.py
from pathlib import Path

import pytest


@pytest.fixture
def tmp_context(tmp_path):
    """Create a temporary context directory with a minimal identity file."""
    identity = tmp_path / "identity.md"
    identity.write_text("You are a test assistant.")
    return tmp_path


@pytest.fixture
def tmp_skills(tmp_path):
    """Create a temporary skills directory."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    return skills_dir
