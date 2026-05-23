# src/scheduler.py
"""Async scheduler that fires scheduled tasks via agent.handle()."""

import asyncio
import json
import logging
import os
import tempfile
import time

from croniter import croniter

from src.skills import load_skill

logger = logging.getLogger(__name__)


def _schedule_path(config):
    return config.context_dir / "schedules.json"


def _load_tasks(config) -> list[dict]:
    path = _schedule_path(config)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read schedules.json: %s", e)
        return []


def _update_task_fields(config, task_id: str, fields: dict) -> None:
    """Atomically merge ``fields`` into the task with id ``task_id``."""
    path = _schedule_path(config)
    try:
        tasks = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return
    for t in tasks:
        if t["id"] == task_id:
            t.update(fields)
            break
    else:
        return  # task not found
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(tasks, f, indent=2)
        os.rename(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _update_last_run(config, task_id: str, timestamp: int) -> None:
    """Back-compat wrapper around :func:`_update_task_fields`."""
    _update_task_fields(config, task_id, {"last_run": timestamp})


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
        _update_task_fields(config, task_id, {
            "last_run": int(time.time()),
            "last_status": "success",
            "last_error": None,
        })
        logger.info("Scheduled task completed: %s", task_id)
    except Exception as e:
        _update_task_fields(config, task_id, {
            "last_status": "error",
            "last_error": str(e)[:500],
        })
        logger.error("Scheduled task failed: %s — %s", task_id, e)


async def run_task_now(agent, task_id: str) -> tuple[bool, str | None]:
    """Fire a single scheduled task immediately.

    Uses the same dispatch path as a real cron tick — ``_run_task`` is
    spawned via ``asyncio.create_task`` and updates ``last_run`` /
    ``last_status`` the same way. Returns once the task is scheduled
    (not awaited); errors surface in ``last_error`` on the next snapshot.
    """
    tasks = _load_tasks(agent.config)
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        return False, f"task '{task_id}' not found."

    prompt = task.get("prompt", "")
    if task.get("skill"):
        skill_content = load_skill(task["skill"], agent.config.skill_dirs)
        if not skill_content.startswith("Skill not found"):
            prompt = skill_content + "\n\n" + prompt

    timestamp = int(time.time())
    session_id = f"sched:{task_id}:{timestamp}"
    _update_task_fields(agent.config, task_id, {"last_attempt_at": timestamp})
    logger.info("Manually firing scheduled task: %s (session %s)", task_id, session_id)
    asyncio.create_task(_run_task(agent, agent.config, task_id, session_id, prompt))
    return True, None


async def _check_and_fire(agent) -> list[str]:
    """Check all tasks and fire any that are due. Returns list of fired task IDs."""
    tasks = _load_tasks(agent.config)
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
            skill_content = load_skill(task["skill"], agent.config.skill_dirs)
            if not skill_content.startswith("Skill not found"):
                prompt = skill_content + "\n\n" + prompt

        timestamp = int(now)
        session_id = f"sched:{task_id}:{timestamp}"

        # Mark the attempt before dispatch so a slow or crashed task does not
        # re-fire on the next tick. last_run only advances on success.
        _update_task_fields(agent.config, task_id, {"last_attempt_at": timestamp})

        logger.info("Firing scheduled task: %s (session %s)", task_id, session_id)
        asyncio.create_task(_run_task(agent, agent.config, task_id, session_id, prompt))
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
