"""Bootstrap the context directory from context.default/ on first run.

Copies any files from context.default/ that don't already exist in context/.
Never overwrites existing files — once curunir has run, the user's data is safe.
"""

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DIR = Path("context.default")
PERSONAS_DIR = Path("personas")


def bootstrap_context(context_dir: Path) -> None:
    """Ensure context_dir exists with baseline files from context.default/.

    Walks context.default/ and copies each file into context_dir only if
    the destination doesn't already exist. Creates intermediate directories
    as needed.
    """
    if not DEFAULT_DIR.is_dir():
        logger.debug("No context.default/ found, skipping bootstrap")
        return

    context_dir.mkdir(parents=True, exist_ok=True)

    for src in DEFAULT_DIR.rglob("*"):
        if not src.is_file():
            continue
        relative = src.relative_to(DEFAULT_DIR)
        dest = context_dir / relative
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        logger.info("Bootstrapped %s", dest)


def bootstrap_persona(context_dir: Path, persona_name: str) -> None:
    """Copy personas/<name>/expertise/* into context_dir/persona/ on first run.

    Mirrors bootstrap_context's non-overwriting semantics: existing files are
    left untouched, so user edits survive restarts. Missing expertise/ dir is
    a silent no-op.
    """
    src_dir = PERSONAS_DIR / persona_name / "expertise"
    if not src_dir.is_dir():
        logger.debug("No expertise/ for persona %s, skipping", persona_name)
        return

    for src in sorted(src_dir.rglob("*")):
        if not src.is_file():
            continue
        relative = src.relative_to(src_dir)
        dest = context_dir / "persona" / relative
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        logger.info("Bootstrapped persona file %s", dest)
