# src/persona.py
"""Persona bundle loading — resolves personas/<name>/persona.yaml at boot.

A persona is a deployment bundle:

- an optional absolute skill allowlist (omit `skills:` to allow every skill
  on disk — this is what `personas/default/` does);
- a `prompts/` directory of `.md` files layered on top of `context/identity.md`
  in the system prompt;
- an optional list of API-key names (documentation / soft warning only —
  never a hard failure).

Core tools are universal across personas and are not curated here.

When `CURUNIR_PERSONA` is unset, boot loads `personas/default/`. There is no
"no persona" code path.
"""
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PERSONAS_DIR = Path("personas")
DEFAULT_PERSONA = "default"


@dataclass(frozen=True)
class Persona:
    name: str
    description: str
    skills: list[str] | None  # None = no allowlist; every skill on disk
    keys: list[str] = field(default_factory=list)


def persona_dir(name: str) -> Path:
    return PERSONAS_DIR / name


def prompts_dir(name: str) -> Path:
    """Where a persona's system-prompt overlay files live."""
    return persona_dir(name) / "prompts"


def load_persona(name: str) -> Persona:
    """Load and validate personas/<name>/persona.yaml.

    Raises FileNotFoundError if the bundle/manifest is missing, ValueError if
    the manifest is malformed.
    """
    manifest = persona_dir(name) / "persona.yaml"
    if not manifest.exists():
        raise FileNotFoundError(
            f"Persona '{name}' not found: expected manifest at {manifest}. "
            "Set CURUNIR_PERSONA to a directory under personas/."
        )
    try:
        data = yaml.safe_load(manifest.read_text()) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Malformed persona manifest {manifest}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"Persona manifest {manifest} must be a YAML mapping")

    skills = data.get("skills")
    if skills is not None:
        if not isinstance(skills, list) or not skills:
            raise ValueError(
                f"Persona manifest {manifest} 'skills:' must be a non-empty "
                "list if present (omit the field entirely to allow every skill)"
            )
        skills = [str(s) for s in skills]
    keys = data.get("keys") or []

    return Persona(
        name=str(data.get("name", name)),
        description=str(data.get("description", "")),
        skills=skills,
        keys=[str(k) for k in keys],
    )


def warn_missing_keys(persona: Persona, environ: Mapping[str, str]) -> list[str]:
    """Log a soft warning for each declared key absent from the environment.

    Returns the list of missing key names (for testing). Never raises.
    """
    missing = [k for k in persona.keys if not environ.get(k)]
    for k in missing:
        logger.warning(
            "persona '%s' expects %s but it is unset in the environment",
            persona.name, k,
        )
    return missing
