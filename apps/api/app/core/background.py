"""Fire-and-forget asyncio tasks whose exceptions are not silently dropped.

Convention 5 (CLAUDE.md) rules out `BackgroundTasks` and mandates a tracked
`asyncio.Task` held in a module-level set. That keeps the task from being GC'd
mid-flight, but it does NOT report a task that dies: when the only reference is
the loop's own weak one (or a set that just `discard`s it), an exception that
escapes the task's body is logged by asyncio at most, and never reaches Sentry.

This adds the missing half:
  * `spawn_tracked(coro, name=...)` — create + hold a reference + report on death.
  * `report_result(task)` — an `add_done_callback` target for tasks whose
    reference is already held elsewhere (the orphan-task services), so their
    escape-hatch failures are reported too.

Both ignore `CancelledError` (that is shutdown, not a failure) and route real
exceptions through `alerting.capture_exc` with a `task` tag.
"""

from __future__ import annotations

import asyncio
import logging

from app.core import alerting

logger = logging.getLogger("mm.api")

#: Holds references to tasks spawned via `spawn_tracked` so the loop's weak
#: reference cannot let one be collected before it finishes.
_TASKS: set[asyncio.Task] = set()


def report_result(task: asyncio.Task) -> None:
    """`add_done_callback` target: report a task that died to Sentry.

    Safe to attach to any fire-and-forget task, including one whose reference is
    held in another module's set. A cancelled task (shutdown) is ignored.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    name = task.get_name()
    logger.warning("background task %r failed: %s", name, exc)
    alerting.capture_exc(exc, tags={"task": name})


def spawn_tracked(coro, *, name: str) -> asyncio.Task:
    """Start a fire-and-forget task, hold a reference, and report if it dies."""
    task = asyncio.create_task(coro, name=name)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    task.add_done_callback(report_result)
    return task
