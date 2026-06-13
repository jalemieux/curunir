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


def _build_task_prompt(config, task: dict) -> str:
    """The effective prompt for a task: the named skill's content (if any,
    resolved through the persona allowlist) prepended to the task's prompt."""
    prompt = task["prompt"]
    if task.get("skill"):
        allowlist = set(config.skill_allowlist) if config.skill_allowlist else None
        skill_content = load_skill(task["skill"], config.skill_dirs,
                                   allowlist=allowlist)
        if not skill_content.startswith("Skill not found"):
            prompt = skill_content + "\n\n" + prompt
    return prompt


async def fire_task(agent, config, task: dict, *, record_run: bool = True,
                    session_id: str | None = None):
    """Run one task through ``agent.handle()`` exactly as the scheduler does.

    The single reusable firing node shared by the scheduler loop and the
    local-UI "Run now" button. Loads + prepends the task's skill, runs in
    system-task mode under a ``sched:<id>:<ts>`` session, and returns the
    agent's response (``None`` on failure).

    ``record_run`` gates run-metadata: when True, ``last_run``/``last_status``
    are stamped via ``mark_run`` (the scheduler loop also stamps
    ``mark_attempt`` *before* dispatch). When False — the manual "Run now"
    path — no metadata is written, so the task's cron cadence is untouched.

    Exceptions from ``handle()`` are swallowed (logged, and recorded via
    ``mark_run`` only when ``record_run``) so a fire-and-forget background task
    can never crash its caller. Dispatched via ``asyncio.create_task()``."""
    task_id = task["id"]
    prompt = _build_task_prompt(config, task)
    sid = session_id or f"sched:{task_id}:{int(time.time())}"
    try:
        response = await agent.handle(
            message="",
            session_id=sid,
            system_task_prompt=prompt,
        )
        if record_run:
            engine.mark_run(_db(config), task_id, int(time.time()), "success")
        logger.info("Scheduled task completed: %s", task_id)
        return response
    except Exception as e:
        if record_run:
            engine.mark_run(_db(config), task_id, 0, "error", error=str(e)[:500])
        logger.error("Scheduled task failed: %s — %s", task_id, e)
        return None


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
        timestamp = int(now)
        session_id = f"sched:{task_id}:{timestamp}"

        # Mark the attempt before dispatch so a slow or crashed task does not
        # re-fire on the next tick. last_run only advances on success.
        engine.mark_attempt(_db(config), task_id, timestamp)

        logger.info("Firing scheduled task: %s (session %s)", task_id, session_id)
        asyncio.create_task(
            fire_task(agent, config, task, record_run=True, session_id=session_id))
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
