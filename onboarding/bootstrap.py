"""Bootstrap the context directory from context.default/ on first run.

Copies any files from context.default/ that don't already exist in context/.
Never overwrites existing files — once curunir has run, the user's data is safe.

Only user-edited content lives in context/ (identity, memory, schedules,
conversations). Framework-level prompt content lives in personas/<name>/prompts/
and is read directly from there at boot — see src/agent/system_prompt.py.
"""

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DIR = Path("context.default")

# Container-level files in context.default/. They are seeded once into the
# container's shared dir by bootstrap_shared, never into an agent's context.
SHARED_FILES = ("profile.md",)

# Where the user profile lived before it became one file per container.
LEGACY_PROFILE = Path("memory") / "profile.md"


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
        if relative.as_posix() in SHARED_FILES:
            continue
        dest = context_dir / relative
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        logger.info("Bootstrapped %s", dest)


def bootstrap_shared(shared_dir: Path, agent_context_dirs: list[Path]) -> None:
    """Set up the container's shared state files, moving the legacy profile.

    The user profile is one file per container, ``<shared>/profile.md``
    (docs/superpowers/specs/2026-09-26-agents-and-containers-design.md,
    Decided #3). ``agent_context_dirs`` lists every agent's context dir,
    default agent first:

    1. If the shared profile does not exist, the first agent that has a
       legacy ``memory/profile.md`` has it moved there. An existing shared
       profile is never overwritten.
    2. Any legacy ``memory/profile.md`` still left (a sibling's bootstrapped
       copy, or one that lost to an existing shared profile) is renamed to
       ``memory/profile.md.legacy`` so no agent keeps a second, diverging
       profile. Nothing is deleted.
    3. If there is still no shared profile, it is seeded from
       ``context.default/profile.md``.
    """
    shared_dir = Path(shared_dir)
    target = shared_dir / "profile.md"

    for context_dir in agent_context_dirs:
        legacy = Path(context_dir) / LEGACY_PROFILE
        if legacy.is_file() and not target.exists():
            shared_dir.mkdir(parents=True, exist_ok=True)
            legacy.rename(target)
            logger.info("Moved legacy profile %s -> %s", legacy, target)

    for context_dir in agent_context_dirs:
        legacy = Path(context_dir) / LEGACY_PROFILE
        if not legacy.is_file():
            continue
        parked = legacy.with_name(legacy.name + ".legacy")
        if parked.exists():
            logger.warning(
                "Legacy profile %s left in place: %s already exists. The "
                "shared profile is %s; merge by hand and delete it.",
                legacy, parked, target,
            )
            continue
        legacy.rename(parked)
        logger.warning(
            "Legacy profile %s renamed to %s: the shared profile %s already "
            "exists. Merge anything missing by hand.", legacy, parked, target,
        )

    template = DEFAULT_DIR / "profile.md"
    if not target.exists() and template.is_file():
        shared_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template, target)
        logger.info("Bootstrapped %s", target)
