"""The memory census never raises, and reports what grew between two calls."""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter

from app.core import memory_telemetry


class _Leaky:
    pass


def test_a_census_reports_counts_and_what_grew():
    first, counts = memory_telemetry.snapshot()
    assert first["objects"] > 0 and first["top"]
    assert "grew" not in first
    held = [_Leaky() for _ in range(5000)]
    second, _ = memory_telemetry.snapshot(counts)
    grew = dict(second["grew"])
    assert grew.get("_Leaky", 0) >= 5000
    assert held  # keep them alive across the second census


def test_the_loop_survives_a_failing_census(monkeypatch, caplog):
    calls = []

    def boom(previous: Counter | None = None):
        calls.append(1)
        raise RuntimeError("census failed")

    monkeypatch.setattr(memory_telemetry, "snapshot", boom)
    monkeypatch.setattr(memory_telemetry, "FIRST_AFTER_S", 0)
    monkeypatch.setattr(memory_telemetry, "INTERVAL_S", 0.01)

    async def run():
        task = asyncio.create_task(memory_telemetry.run_forever("test"))
        await asyncio.sleep(0.1)
        task.cancel()

    asyncio.run(run())
    assert len(calls) >= 2


def _run_loop(monkeypatch, settled_mb: list[int], ceiling_mb: int) -> list[int]:
    """Drive run_forever over a scripted sequence of post-trim RSS samples and
    return the sample index (1-based) at which on_recycle fired, if any."""
    samples = iter(settled_mb)
    seen: list[int] = []
    fired: list[int] = []

    def fake(previous: Counter | None = None):
        mb = next(samples)
        seen.append(mb)
        return {"rss_kb": mb * 1024, "rss_kb_after_trim": mb * 1024}, Counter()

    monkeypatch.setattr(memory_telemetry, "snapshot", fake)
    monkeypatch.setattr(memory_telemetry, "FIRST_AFTER_S", 0)
    monkeypatch.setattr(memory_telemetry, "INTERVAL_S", 0)

    async def run():
        task = asyncio.create_task(
            memory_telemetry.run_forever(
                "test",
                recycle_above_mb=ceiling_mb,
                on_recycle=lambda: fired.append(len(seen)),
            )
        )
        for _ in range(200):
            if task.done() or len(seen) >= len(settled_mb):
                break
            await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, StopIteration):
            await task

    asyncio.run(run())
    return fired


def test_recycles_after_two_consecutive_samples_over_the_ceiling(monkeypatch):
    # One sample over (a replay caught mid-flight) is not enough; two in a row is.
    assert _run_loop(monkeypatch, [200, 320, 250, 310, 330, 340], 300) == [5]


def test_never_recycles_under_the_ceiling(monkeypatch):
    assert _run_loop(monkeypatch, [200, 290, 299, 300, 250], 300) == []


def test_no_ceiling_means_no_recycle(monkeypatch):
    fired: list[int] = []
    monkeypatch.setattr(
        memory_telemetry,
        "snapshot",
        lambda previous=None: (
            {"rss_kb": 10**7, "rss_kb_after_trim": 10**7},
            Counter(),
        ),
    )
    monkeypatch.setattr(memory_telemetry, "FIRST_AFTER_S", 0)
    monkeypatch.setattr(memory_telemetry, "INTERVAL_S", 0)

    async def run():
        task = asyncio.create_task(memory_telemetry.run_forever("test"))
        for _ in range(20):
            await asyncio.sleep(0)
        assert not task.done()
        task.cancel()

    asyncio.run(run())
    assert fired == []


def test_a_supervised_worker_asks_its_parent_for_a_replacement(monkeypatch):
    import os
    import signal

    from app.pos_serve import SUPERVISED_ENV

    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))

    monkeypatch.delenv(SUPERVISED_ENV, raising=False)
    memory_telemetry.ask_supervisor_to_replace()
    assert sent == []  # plain uvicorn / tests: never signal an arbitrary parent

    monkeypatch.setenv(SUPERVISED_ENV, "1")
    memory_telemetry.ask_supervisor_to_replace()
    assert sent == [(os.getppid(), signal.SIGHUP)]


def test_keeps_asking_if_the_replacement_never_comes(monkeypatch):
    # Two streaks of two over-ceiling samples: asked twice, not once and silent.
    # (a trailing sample lets the fourth one be acted on before the helper stops)
    assert _run_loop(monkeypatch, [310, 320, 330, 340, 100], 300) == [2, 4]
