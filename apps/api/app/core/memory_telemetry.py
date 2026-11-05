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

**What it found, and the recycle.** Not a leak: after an estate costing replay
(43k ledger lines, ~140 MB of live objects at its peak) the scheduler's object
count fell straight back, but its RSS stayed ~75 MB higher and `malloc_trim`
won't return it. The peak's pages stay pinned in CPython's arenas by the few
long-lived objects allocated beside it. The next bigger replay ratchets RSS up
again, and the ledger only grows, so over days the container reached its limit
and swapped. So a process may pass `recycle_above_mb`: once its RSS *after the
trim* is above that for `_RECYCLE_AFTER` samples in a row (which rules out
catching a replay mid-flight), `on_recycle` is called. The scheduler uses it to
exit cleanly and have its start script relaunch only the scheduler. Its loops
are restart-safe by design: every deploy restarts them. The register API's
worker uses `ask_supervisor_to_replace`: `app.pos_serve` brings a fresh worker
up before it drains this one, so a till never sees the port go away.
"""

from __future__ import annotations

import asyncio
import ctypes
import gc
import json
import logging
import os
import signal
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["INTERVAL_S", "ask_supervisor_to_replace", "run_forever", "snapshot"]

#: Between lines. The first comes `FIRST_AFTER_S` after boot, as the baseline.
INTERVAL_S = 900
FIRST_AFTER_S = 120
_TOP = 15
#: Consecutive over-ceiling samples (each `INTERVAL_S` apart) before recycling.
_RECYCLE_AFTER = 2

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


def ask_supervisor_to_replace() -> None:
    """Ask `app.pos_serve`'s supervisor to replace this worker, new before old.

    A no-op (logged) when this process has no such supervisor: under plain
    `uvicorn`, in tests, locally. SIGHUP to an arbitrary parent could kill it.
    """
    from app.pos_serve import SUPERVISED_ENV

    if not os.environ.get(SUPERVISED_ENV):
        logger.warning("memory.recycle skipped: no supervisor to replace this worker")
        return
    os.kill(os.getppid(), signal.SIGHUP)


async def run_forever(
    role: str,
    *,
    recycle_above_mb: int | None = None,
    on_recycle: Callable[[], None] | None = None,
) -> None:
    """The per-process loop. Cancelled on shutdown; never dies on an error.

    With `recycle_above_mb` and `on_recycle`, calls `on_recycle` each time RSS
    after the trim has stayed above the ceiling for `_RECYCLE_AFTER` samples.
    """
    started = time.monotonic()
    previous: Counter | None = None
    over = 0
    await asyncio.sleep(FIRST_AFTER_S)
    while True:
        try:
            data, previous = await asyncio.to_thread(snapshot, previous)
            data["role"] = role
            data["uptime_s"] = int(time.monotonic() - started)
            logger.info("memory.telemetry %s", json.dumps(data, default=str))
            settled_kb = data["rss_kb_after_trim"] or data["rss_kb"]
            if recycle_above_mb and on_recycle and settled_kb:
                over = over + 1 if settled_kb > recycle_above_mb * 1024 else 0
                if over >= _RECYCLE_AFTER:
                    logger.warning(
                        "memory.recycle role=%s rss_after_trim_mb=%d ceiling_mb=%d "
                        "samples_over=%d uptime_s=%d",
                        role,
                        settled_kb // 1024,
                        recycle_above_mb,
                        over,
                        data["uptime_s"],
                    )
                    on_recycle()
                    # Keep sampling: if the replacement doesn't come (the
                    # supervisor gave up), ask again after another streak.
                    over = 0
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — telemetry must never take a process down
            logger.exception("memory telemetry failed; retrying next interval")
        await asyncio.sleep(INTERVAL_S)
