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


# --- system jobs ----------------------------------------------------------
# Code-registered jobs (e.g. the re-engagement nudge). Unlike user tasks in
# schedules.json, these are infrastructure: the LLM `schedule` tool cannot
# see, edit, or disable them. Each job is a duck-typed object exposing:
#   .id   — str, unique
#   .cron — str, croniter expression
#   async .run(agent) — performs the job
# `last_run` is held in memory only — system jobs need no on-disk state.
SYSTEM_JOBS: list = []
_SYSTEM_JOB_LAST_RUN: dict[str, float] = {}


def register_system_job(job) -> None:
    """Register a code-defined job into the shared scheduler tick."""
    SYSTEM_JOBS.append(job)
    logger.info("Registered system job: %s (cron %s)", job.id, job.cron)


async def _run_system_job(job, agent) -> None:
    """Run one system job, swallowing exceptions so the tick stays alive."""
    try:
        await job.run(agent)
        logger.info("System job completed: %s", job.id)
    except Exception as e:
        logger.error("System job failed: %s — %s", job.id, e)


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

    # Code-registered system jobs share the same croniter due-check. Their
    # last_run is in-memory; marking it before dispatch prevents a same-tick
    # double fire while a slow job is still running.
    for job in SYSTEM_JOBS:
        marker = {"cron": job.cron, "last_run": _SYSTEM_JOB_LAST_RUN.get(job.id, 0)}
        if not _is_due(marker, now):
            continue
        _SYSTEM_JOB_LAST_RUN[job.id] = int(now)
        logger.info("Firing system job: %s", job.id)
        asyncio.create_task(_run_system_job(job, agent))
        fired.append(job.id)

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
