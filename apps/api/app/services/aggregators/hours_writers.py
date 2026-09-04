"""The write half of the hours sync — pushing a branch's schedule to a channel.

Mirror image of `menu_readers._HOURS_READERS`: that reads each marketplace's
opening hours, this would write them. **No channel has a writer yet.** Every
push reachability is documented in `docs/integrator-capabilities.md:136-159`, and
several channels (Keeta, Deliveroo, Noon) can only be written through the headed
anti-bot worker on the VM, while Careem/Talabat still need their save endpoint
probed. So this is the seam the branch-hours cron calls, not a working writer:
each entry raises `NotImplementedError`, the cron catches it and moves on, and a
channel becomes live by landing its writer here behind `CATALOG_SYNC_ENABLED`.

Two operations, because a marketplace has no holiday concept: on an ordinary day
`push_hours` sends the day's window; on a closed day (holiday or a closed
weekday) `close_outlet` snoozes the outlet, and the next open day's `push_hours`
reopens it.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "HoursWriteUnsupported",
    "supported_channels",
    "push_hours",
    "close_outlet",
]


class HoursWriteUnsupported(NotImplementedError):
    """No writer exists for this channel yet. The cron treats it as skip-and-log."""


def supported_channels() -> frozenset[str]:
    """The channels a live hours writer exists for — none yet."""
    return frozenset()


async def push_hours(
    db: Any, *, channel: str, branch: Any, opens: str, closes: str
) -> None:
    """Send today's `opens`–`closes` window to `channel` for `branch`.

    Unbuilt — see the module docstring. Kept as the single call site the cron
    uses so wiring a real writer is one change here, not a new branch in the loop.
    """
    raise HoursWriteUnsupported(
        f"no hours writer for {channel} yet — window would be {opens}-{closes}"
    )


async def close_outlet(db: Any, *, channel: str, branch: Any) -> None:
    """Snooze `branch`'s outlet on `channel` for a closed day (holiday/closed weekday).

    Unbuilt — see the module docstring.
    """
    raise HoursWriteUnsupported(f"no outlet-close writer for {channel} yet")
