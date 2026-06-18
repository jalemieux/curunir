"""Per-agent-loop correlation id for log lines.

Each ``Agent.handle()`` call is one "loop". Tagging every log record it
produces — including records emitted by modules it calls into, e.g.
``src.llm`` retry warnings or tool executors — with a shared id lets you
grep a single loop out of an interleaved multi-session / multi-task log.

A ``ContextVar`` carries the id so it propagates automatically across
``await`` boundaries and into the tasks spawned for the concurrent tool
batch, while staying isolated between concurrently-running ``handle()``
calls. The ``LoopIdFilter`` is attached to the root log handlers so the
``%(loop_id)s`` format field resolves for records from any module.
"""
from __future__ import annotations

import contextvars
import logging

# Empty string means "not inside an agent loop" (scheduler ticks, channel
# bookkeeping, startup); rendered as "-" by the filter.
loop_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "loop_id", default=""
)


class LoopIdFilter(logging.Filter):
    """Stamp each record with the active loop id as ``loop_id`` (``-`` if none).

    Always returns ``True`` — it filters nothing, it only annotates.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.loop_id = loop_id_var.get() or "-"
        return True
