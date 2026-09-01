"""The daemon's in-process job queue: one priority queue, one consumer.

The always-on worker (`daemon.py`) has exactly one consumer draining this queue,
which is what replaces the OS `flock` the host-cron model relied on: at most one
job runs at a time, so at most one Chrome is ever spawned — the RAM guarantee on
the e2-small. Priority lets a RELOGIN (a dead session, blocking every downstream
ingest) jump ahead of a queued cookie WARM or data pull. Dedup on `(kind, channel)`
keeps a slow scheduler tick or a burst of heal polls from stacking the same work.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from enum import IntEnum


class JobKind(IntEnum):
    """Job kinds, in priority order — a LOWER value is dequeued first.

    RELOGIN preempts everything: a dead session blocks the API's ingest for that
    channel, so healing it matters more than rotating a cookie (WARM), pulling
    Keeta's masked-soon orders (KEETA_ORDERS), or fetching finance
    (DELIVEROO_FINANCE / KEETA_FINANCE). The int values ARE the priority, so
    `JobKind` compares directly in the queue's ordering.

    Keeta is split into two jobs on purpose: ORDERS is time-critical (Keeta masks
    the customer/address a few hours after the order) and quick, so it runs every
    few hours at a middling priority; FINANCE re-downloads settled statement files
    and is slow, so it runs once nightly at the LOWEST priority — never blocking a
    heal or an orders pull behind its long download.
    """

    RELOGIN = 0
    WARM = 1
    KEETA_ORDERS = 2
    DELIVEROO_FINANCE = 3
    KEETA_FINANCE = 4
    #: The catalog-sync menu read — not time-critical (the menu changes rarely), so
    #: it sits at the lowest priority, never blocking a heal or an orders pull.
    KEETA_MENU = 5


@dataclass(order=True)
class Job:
    """One unit of browser work. Ordered by `(kind, seq)`: kind is the priority
    (RELOGIN first), and `seq` is a monotonic tie-break so equal-priority jobs run
    first-in-first-out rather than in an arbitrary order. `channel` is carried but
    not compared — two jobs never tie on `seq`."""

    kind: JobKind
    seq: int
    channel: str = field(default="", compare=False)


class JobQueue:
    """An `asyncio.PriorityQueue` of `Job`s with `(kind, channel)` dedup.

    `put` returns False when an identical job is already queued or in flight (it
    stays "pending" from `put` until `complete`), so the scheduler can enqueue
    freely on every tick without piling up duplicates. `get`/`complete` are the
    consumer's side; `complete` must be called once per got job to release both the
    dedup slot and the underlying queue's unfinished-task count.
    """

    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue[Job] = asyncio.PriorityQueue()
        self._pending: set[tuple[JobKind, str]] = set()
        self._seq = itertools.count()

    async def put(self, kind: JobKind, channel: str = "") -> bool:
        """Enqueue a job unless the same `(kind, channel)` is already pending.

        Returns True if it was enqueued, False if it was a duplicate. The pending
        check and insert happen before any real await (the queue is unbounded, so
        `put` never blocks), so two near-simultaneous callers cannot both enqueue.
        """
        key = (kind, channel)
        if key in self._pending:
            return False
        self._pending.add(key)
        await self._queue.put(Job(kind=kind, seq=next(self._seq), channel=channel))
        return True

    async def get(self) -> Job:
        """Block until the highest-priority job is available, and return it."""
        return await self._queue.get()

    def complete(self, job: Job) -> None:
        """Mark a got job done: free its dedup slot and the queue's task count."""
        self._pending.discard((job.kind, job.channel))
        self._queue.task_done()

    def pending(self) -> set[tuple[JobKind, str]]:
        """A snapshot of what is queued or in flight — for tests/introspection."""
        return set(self._pending)
