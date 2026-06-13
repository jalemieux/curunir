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


def _build_prompt(config, task: dict) -> str:
    """Compute the system-task prompt, prepending the named skill if any.
    Shared by the scheduler loop and the run-now path so both fire identically."""
    prompt = task["prompt"]
    if task.get("skill"):
        allowlist = set(config.skill_allowlist) if config.skill_allowlist else None
        skill_content = load_skill(task["skill"], config.skill_dirs, allowlist=allowlist)
        if not skill_content.startswith("Skill not found"):
            prompt = skill_content + "\n\n" + prompt
    return prompt


async def fire_task(agent, config, task: dict, *, record_run: bool,
                    session_id: str) -> None:
    """Fire one scheduled task via ``agent.handle()`` in system-task mode.

    The single reusable node behind both the scheduler loop and the local-UI
    "Run now" route — a real fire and a test fire share the exact same path
    (skill prepend → ``handle()``). They differ only in policy, gated by
    ``record_run``:

    - ``record_run=True`` (scheduler): stamp ``mark_attempt`` before dispatch
      and ``mark_run`` on completion — this counts as the scheduled run.
    - ``record_run=False`` (run-now test fire): stamp **neither**, leaving the
      cron cadence and next-due untouched.

    Always fire-and-forget safe: a failure is logged (and recorded only when
    ``record_run``) but never propagates out of the coroutine.
    """
    task_id = task["id"]
    prompt = _build_prompt(config, task)

    if record_run:
        # Mark the attempt before dispatch so a slow or crashed task does not
        # re-fire on the next tick. last_run only advances on success.
        engine.mark_attempt(_db(config), task_id, int(time.time()))

    try:
        await agent.handle(
            message="",
            session_id=session_id,
            system_task_prompt=prompt,
        )
        if record_run:
            engine.mark_run(_db(config), task_id, int(time.time()), "success")
        logger.info("Scheduled task completed: %s (record_run=%s)",
                    task_id, record_run)
    except Exception as e:
        if record_run:
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
        session_id = f"sched:{task_id}:{int(now)}"

        # fire_task stamps mark_attempt synchronously (before its first await)
        # under record_run=True, so a second tick in the same loop iteration
        # sees this task as no-longer-due and can't double-fire it.
        logger.info("Firing scheduled task: %s (session %s)", task_id, session_id)
        asyncio.create_task(fire_task(
            agent, config, task, record_run=True, session_id=session_id,
        ))
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
