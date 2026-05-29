# src/persona.py
"""Persona bundle loading — resolves personas/<name>/persona.yaml at boot.

A persona curates a deployment: an absolute skill allowlist, an optional core
tool allowlist, and a list of API-key names (documentation / soft warning
only — never a hard failure). Expertise prompt files live alongside in
personas/<name>/expertise/ and are bootstrapped into context/persona/
separately (see onboarding/bootstrap.py).
"""
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PERSONAS_DIR = Path("personas")


@dataclass(frozen=True)
class Persona:
    name: str
    description: str
    skills: list[str]
    tools: list[str] | None  # None = all default tools
    keys: list[str] = field(default_factory=list)


def persona_dir(name: str) -> Path:
    return PERSONAS_DIR / name


def load_persona(name: str) -> Persona:
    """Load and validate personas/<name>/persona.yaml.

    Raises FileNotFoundError if the bundle/manifest is missing, ValueError if
    the manifest is malformed or omits the required 'skills:' list.
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
    if not isinstance(skills, list) or not skills:
        raise ValueError(
            f"Persona manifest {manifest} must list at least one skill "
            "under 'skills:'"
        )
    tools = data.get("tools")
    if tools is not None and not isinstance(tools, list):
        raise ValueError(
            f"Persona manifest {manifest} 'tools:' must be a list if present"
        )
    keys = data.get("keys") or []

    return Persona(
        name=str(data.get("name", name)),
        description=str(data.get("description", "")),
        skills=[str(s) for s in skills],
        tools=[str(t) for t in tools] if tools is not None else None,
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
