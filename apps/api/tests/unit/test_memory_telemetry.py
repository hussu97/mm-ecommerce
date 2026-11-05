"""The memory census never raises, and reports what grew between two calls."""

from __future__ import annotations

import asyncio
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
