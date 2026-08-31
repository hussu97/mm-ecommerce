"""The daemon's priority queue: ordering, FIFO tie-break, and (kind, channel) dedup."""

from __future__ import annotations

from aggregator_bootstrap.queue import Job, JobKind, JobQueue


def test_jobkind_priority_order():
    # Lower value = higher priority = dequeued first.
    assert (
        JobKind.RELOGIN < JobKind.WARM < JobKind.KEETA_PULL < JobKind.DELIVEROO_FINANCE
    )


async def test_get_returns_highest_priority_first():
    q = JobQueue()
    # Enqueue out of priority order.
    assert await q.put(JobKind.KEETA_PULL, "keeta")
    assert await q.put(JobKind.WARM, "noon")
    assert await q.put(JobKind.RELOGIN, "talabat")

    kinds = [(await q.get()).kind for _ in range(3)]
    assert kinds == [JobKind.RELOGIN, JobKind.WARM, JobKind.KEETA_PULL]


async def test_fifo_within_a_priority():
    q = JobQueue()
    assert await q.put(JobKind.WARM, "noon")
    assert await q.put(JobKind.WARM, "talabat")

    first = await q.get()
    second = await q.get()
    assert (first.channel, second.channel) == ("noon", "talabat")


async def test_dedupes_same_kind_and_channel_while_pending():
    q = JobQueue()
    assert await q.put(JobKind.WARM, "noon") is True
    # Same (kind, channel) is already queued → refused.
    assert await q.put(JobKind.WARM, "noon") is False
    # A different channel of the same kind is fine.
    assert await q.put(JobKind.WARM, "talabat") is True
    assert q.pending() == {(JobKind.WARM, "noon"), (JobKind.WARM, "talabat")}


async def test_in_flight_job_still_dedupes_until_completed():
    q = JobQueue()
    assert await q.put(JobKind.KEETA_PULL, "keeta") is True
    job = await q.get()
    # Dequeued but not completed → still "in flight", so a re-enqueue is refused.
    assert await q.put(JobKind.KEETA_PULL, "keeta") is False
    q.complete(job)
    # Once completed, the same job may be scheduled again.
    assert await q.put(JobKind.KEETA_PULL, "keeta") is True


async def test_complete_clears_only_that_key():
    q = JobQueue()
    await q.put(JobKind.WARM, "noon")
    await q.put(JobKind.RELOGIN, "noon")  # same channel, different kind → distinct
    job = await q.get()  # RELOGIN/noon (higher priority)
    assert job == Job(kind=JobKind.RELOGIN, seq=job.seq, channel="noon")
    q.complete(job)
    assert q.pending() == {(JobKind.WARM, "noon")}
