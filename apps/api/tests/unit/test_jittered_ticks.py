"""Loop ticks are jittered so fixed-interval sweeps drift out of phase.

Aligned wakeups are the multiplier behind the scheduler-pool `QueuePool ...
timed out` storm: each sweep takes two scheduler connections, so a few loops
waking together drain the six-slot pool. `jittered` only perturbs the sleep
duration, never what a sweep does, so it is safe on every loop.
"""

from __future__ import annotations

from app.core.background import jittered


def test_jitter_stays_within_the_band():
    for _ in range(1000):
        v = jittered(100, frac=0.15)
        assert 85.0 <= v <= 115.0


def test_jitter_actually_varies():
    values = {jittered(100) for _ in range(50)}
    assert len(values) > 40, "jitter should spread values, not repeat one"


def test_zero_frac_is_a_noop():
    assert jittered(60, frac=0.0) == 60
