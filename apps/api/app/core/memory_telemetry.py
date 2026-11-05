"""
Per-process memory telemetry, and the glibc trim that tells growth from waste.

**Why it exists.** In November 2026 both API containers sat at their cgroup
limits for days (the storefront at ~700 MB of 704 MiB with ~230 MB swapped out,
the register API over its 384 MiB), and nothing on the box could say whether
that was a leak — something holding objects forever — or the allocator keeping
freed memory it never hands back. Those need opposite fixes, so each process
(web, scheduler, register) now logs one `memory.telemetry` line every
`INTERVAL_S`:

* `rss_kb` / `swap_kb` before and after `malloc_trim(0)`. A large drop after the
  trim is fragmentation: freed memory glibc was sitting on. The trim also gives
  it back, so the line doubles as the mitigation.
* `objects` and the `top` gc-tracked types by count, plus `grew`: the types whose
  count rose most since the previous line. A leak shows as one type climbing
  line after line; a plateau shows as noise.
* the sizes of the caches that are bounded by design (SQLAlchemy's compiled
  cache per engine, the tracked background tasks) so a bound that is not
  holding is visible too.

The census walks every gc-tracked object (~0.5 s of CPU on this box) in a
worker thread every quarter hour: cheap next to what it answers, and the loop
keeps serving meanwhile. It never raises.
"""

from __future__ import annotations

import asyncio
import ctypes
import gc
import json
import logging
import time
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["INTERVAL_S", "run_forever", "snapshot"]

#: Between lines. The first comes `FIRST_AFTER_S` after boot, as the baseline.
INTERVAL_S = 900
FIRST_AFTER_S = 120
_TOP = 15

_libc: Any = None
try:  # glibc only (the API image); macOS and musl simply skip the trim.
    _libc = ctypes.CDLL("libc.so.6")
    _libc.malloc_trim.argtypes = [ctypes.c_size_t]
    _libc.malloc_trim.restype = ctypes.c_int
except (OSError, AttributeError):
    _libc = None


def _status_kb() -> dict[str, int]:
    """VmRSS and VmSwap of this process, in kB (empty off Linux)."""
    out: dict[str, int] = {}
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                if key in ("VmRSS", "VmSwap"):
                    out[key] = int(rest.split()[0])
    except OSError:
        pass
    return out


def _compiled_cache_sizes() -> dict[str, int]:
    sizes: dict[str, int] = {}
    try:
        from app.core.database import engine, scheduler_engine

        for name, eng in (("request", engine), ("scheduler", scheduler_engine)):
            cache = getattr(eng.sync_engine, "_compiled_cache", None)
            if cache is not None:
                sizes[name] = len(cache)
    except Exception:  # noqa: BLE001 — telemetry must never fail
        pass
    return sizes


def snapshot(previous: Counter | None = None) -> tuple[dict[str, Any], Counter]:
    """One census. Returns the loggable dict and the type counts for the next
    call's `grew`. Synchronous: run it in a thread."""
    gc.collect()
    counts: Counter = Counter(type(o).__qualname__ for o in gc.get_objects())
    before = _status_kb()
    trimmed = bool(_libc and _libc.malloc_trim(0))
    after = _status_kb()

    from app.core import background

    data: dict[str, Any] = {
        "rss_kb": before.get("VmRSS"),
        "swap_kb": before.get("VmSwap"),
        "rss_kb_after_trim": after.get("VmRSS") if _libc else None,
        "trimmed": trimmed,
        "objects": sum(counts.values()),
        "top": counts.most_common(_TOP),
        "compiled_cache": _compiled_cache_sizes(),
        "tracked_tasks": len(background._TASKS),
    }
    if previous is not None:
        delta = counts - previous
        data["grew"] = delta.most_common(_TOP)
    return data, counts


async def run_forever(role: str) -> None:
    """The per-process loop. Cancelled on shutdown; never dies on an error."""
    started = time.monotonic()
    previous: Counter | None = None
    await asyncio.sleep(FIRST_AFTER_S)
    while True:
        try:
            data, previous = await asyncio.to_thread(snapshot, previous)
            data["role"] = role
            data["uptime_s"] = int(time.monotonic() - started)
            logger.info("memory.telemetry %s", json.dumps(data, default=str))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — telemetry must never take a process down
            logger.exception("memory telemetry failed; retrying next interval")
        await asyncio.sleep(INTERVAL_S)
