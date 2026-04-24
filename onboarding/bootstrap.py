"""Bootstrap the context directory from context.default/ on first run.

Copies any files from context.default/ that don't already exist in context/.
Never overwrites existing files — once curunir has run, the user's data is safe.
"""

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DIR = Path("context.default")


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
