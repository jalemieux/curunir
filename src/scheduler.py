# src/scheduler.py
"""Async scheduler that fires scheduled tasks via agent.handle().

Reads due tasks from the SQLite schedule store (`context/schedules.db`) each
tick and stamps run metadata back through scoped engine writes — so a user edit
and the scheduler's bookkeeping never clobber each other."""

import asyncio
import logging
import time

from croniter import croniter

from src.schedule_store import db as sdb
from src.schedule_store import engine
from src.skills import load_skill

logger = logging.getLogger(__name__)

# Strong references to in-flight scheduled tasks. asyncio only keeps a *weak*
# reference to a running task, so a fire-and-forget task with no live strong
# reference can be garbage-collected mid-run — silently aborting the task
# before mark_run records its outcome. Holding it here (self-removed via a
# done-callback) keeps it alive for its full lifetime. See issue #493.
_background_tasks: set[asyncio.Task] = set()


def _db(config) -> str:
    return str(config.schedules_db)


def _load_tasks(config) -> list[dict]:
    try:
        sdb.init_db(_db(config))
        return engine.list_schedules(_db(config))
    except Exception as e:  # noqa: BLE001 — a corrupt store must not kill the loop
        logger.warning("Failed to read schedule store: %s", e)
        return []


def _is_due(task: dict, now: float) -> bool:
    """Check if a task is due. Baseline is the latest of last_run / last_attempt_at
    so an in-flight or recently-failed task does not re-fire until its next cron tick."""
    baseline = max(task.get("last_run", 0), task.get("last_attempt_at", 0))
    if baseline >= now:
        return False
    try:
        cron = croniter(task["cron"], baseline)
        next_fire = cron.get_next(float)
        return next_fire <= now
    except (ValueError, KeyError):
        return False


async def _run_task(agent, config, task_id: str, session_id: str, prompt: str) -> None:
    """Run a single scheduled task. Called via asyncio.create_task() for concurrency."""
    try:
        await agent.handle(
            message="",
            session_id=session_id,
            system_task_prompt=prompt,
        )
        engine.mark_run(_db(config), task_id, int(time.time()), "success")
        logger.info("Scheduled task completed: %s", task_id)
    except Exception as e:
        engine.mark_run(_db(config), task_id, 0, "error", error=str(e)[:500])
        logger.error("Scheduled task failed: %s — %s", task_id, e)


async def _check_and_fire(agent) -> list[str]:
    """Check all tasks and fire any that are due. Returns list of fired task IDs."""
    config = agent.config
    tasks = _load_tasks(config)
    fired = []
    now = time.time()

    for task in tasks:
        if not task.get("enabled", True):
            continue
        if not _is_due(task, now):
            continue

        task_id = task["id"]
        prompt = task["prompt"]

        # Load skill content if specified
        if task.get("skill"):
            allowlist = set(config.skill_allowlist) if config.skill_allowlist else None
            skill_content = load_skill(task["skill"], config.skill_dirs,
                                       allowlist=allowlist)
            if not skill_content.startswith("Skill not found"):
                prompt = skill_content + "\n\n" + prompt

        timestamp = int(now)
        session_id = f"sched:{task_id}:{timestamp}"

        # Mark the attempt before dispatch so a slow or crashed task does not
        # re-fire on the next tick. last_run only advances on success.
        engine.mark_attempt(_db(config), task_id, timestamp)

        logger.info("Firing scheduled task: %s (session %s)", task_id, session_id)
        task = asyncio.create_task(_run_task(agent, config, task_id, session_id, prompt))
        # Retain a strong reference so the task cannot be GC'd mid-run; drop it
        # once done (see _background_tasks above).
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        fired.append(task_id)

    return fired


async def run_scheduler(agent, interval_sec: int = 60):
    """Main scheduler loop. Runs forever, checking for due tasks every interval."""
    logger.info("Scheduler started (interval: %ds)", interval_sec)
    while True:
        await asyncio.sleep(interval_sec)
        try:
            fired = await _check_and_fire(agent)
            if fired:
                logger.info("Scheduler tick: fired %s", fired)
        except Exception as e:
            logger.error("Scheduler tick error: %s", e)
