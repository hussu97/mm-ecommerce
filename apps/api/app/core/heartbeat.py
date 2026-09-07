"""A liveness stamp per background loop, so `/health` can see a wedged sweep.

Every `run_forever` loop calls `beat("<name>")` once per tick. `/health` reads
the stamps back and reports each loop's age, so a loop that has stopped ticking
(cancelled, deadlocked, or shut out of leadership on a stale slot) shows up as a
stale heartbeat rather than as silence nobody can distinguish from "idle".

The stamp lives in Redis (shared across the blue/green slots and surviving a
restart) via `cache`, which degrades to a no-op when Redis is down — so a beat
never raises and never blocks a tick, and a missing stamp on `/health` reads as
"unknown", not "dead". A loop beats once per tick regardless of whether it won
its leader lock that tick: the stamp is liveness of the loop TASK (a cancelled
or deadlocked loop stops beating and ages out), not a claim of leadership. A
loop gated off by a feature flag never starts and so never beats — its age reads
as null, the honest "not running here".
"""

from __future__ import annotations

import time

from app.core.cache import cache_get, cache_set

__all__ = ["LOOP_NAMES", "age_seconds", "beat", "key_for"]

#: Every background loop that beats, so `/health` can report an age (or null,
#: "unknown") for each. A name whose loop is gated off by a feature flag (grubops,
#: aggregator) simply never beats and reads as null — which is the honest answer,
#: not a failure. Keep this in step with the `spawn_tracked(..., name=...)` calls
#: in `app_setup.make_lifespan`; the names must match exactly.
LOOP_NAMES = (
    "delivery_scheduler",
    "log_retention",
    "inventory_source_event_sweeper",
    "daily_sales_email",
    "branch_hours_sync",
    "grubops_reconcile",
    "grubops_orders",
    "aggregator_ingest",
)

#: Kept well past the slowest loop's tick (hourly) so a stamp is read back as a
#: real age rather than expiring into "unknown" between beats. A genuinely dead
#: loop's stamp still ages visibly for a day before it lapses.
_HEARTBEAT_TTL_SECONDS = 24 * 3600

_PREFIX = "heartbeat:"


def key_for(name: str) -> str:
    return f"{_PREFIX}{name}"


async def beat(name: str, *, now: float | None = None) -> None:
    """Stamp `name` alive. Never raises (cache degrades to a no-op)."""
    await cache_set(
        key_for(name),
        now if now is not None else time.time(),
        ttl=_HEARTBEAT_TTL_SECONDS,
    )


async def age_seconds(name: str, *, now: float | None = None) -> float | None:
    """Seconds since `name` last beat, or None when there is no stamp."""
    stamped = await cache_get(key_for(name))
    if stamped is None:
        return None
    try:
        return max(0.0, (now if now is not None else time.time()) - float(stamped))
    except (TypeError, ValueError):
        return None
