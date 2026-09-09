"""The delivery loop sweeps before its first full tick (F-COU-18).

It used to `sleep(TICK)` at the top of the loop, so after every container
restart a fresh order waited up to a minute for its first dispatch/arrival pass.
The loop now takes only a short startup pause, sweeps, and sleeps a tick between
sweeps thereafter — including after a failure, so a persistent error still backs
off to the tick cadence instead of spinning.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.delivery import delivery_scheduler as ds


@pytest.mark.asyncio
async def test_it_sweeps_before_the_first_full_tick(monkeypatch):
    events: list[str] = []
    sleeps: list[float] = []

    async def fake_sweep() -> bool:
        events.append("sweep")
        # Stop the otherwise-infinite loop after the first real sweep.
        raise asyncio.CancelledError

    async def fake_beat(_name: str) -> None:
        events.append("beat")

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(ds, "sweep_once", fake_sweep)
    monkeypatch.setattr(ds.heartbeat, "beat", fake_beat)
    monkeypatch.setattr(ds.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await ds.run_forever()

    # Order: a startup pause, then beat, then the sweep — never a full tick first.
    assert events == ["beat", "sweep"]
    # Exactly one sleep happened before the sweep, and it was the SHORT startup
    # pause, not a whole tick.
    assert len(sleeps) == 1
    assert sleeps[0] < ds._TICK_SECONDS
