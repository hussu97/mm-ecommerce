"""The storefront background loops, in their own process.

They used to run inside the HTTP worker's event loop. A heavy aggregator sweep
(152 orders + external HTTP) then monopolised the single worker and drained its
DB pool, so customer checkout stalled for tens of seconds and — under pool
exhaustion — an order's commit landed on a reaped connection and was lost while
the customer was shown success (MM-20260919-007).

This runs the same loops as a *sibling process* in the same container: its own
event loop and its own DB pools, so a sweep can no longer touch a web request.
It is not a new image and adds no deploy step — the container's start script
(`scripts/start-web-and-scheduler.sh`) launches this alongside uvicorn, with the
HTTP worker's `STOREFRONT_SCHEDULER_ENABLED` forced off and this process's forced
on, and brings the whole container down if either process exits, so a dead
scheduler is never hidden behind a healthy HTTP port.

The loops themselves are unchanged and still each hold a database advisory lock,
so a second copy during a blue/green cutover is harmless.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from app.app_setup import (
    configure_observability,
    start_storefront_schedulers,
    stop_storefront_schedulers,
)
from app.core.config import settings

logger = logging.getLogger("mm.api")


async def _run() -> None:
    configure_observability(service="mm-scheduler")

    if not settings.STOREFRONT_SCHEDULER_ENABLED:
        # The start script sets this on for this process; if it is off, the loops
        # would silently never start. Say so loudly and exit non-zero so the
        # container restarts rather than trading with no dispatcher.
        logger.error(
            "scheduler_runner started with STOREFRONT_SCHEDULER_ENABLED off — "
            "nothing to run; exiting so the container is restarted"
        )
        raise SystemExit(1)

    tasks = await start_storefront_schedulers()
    logger.info("scheduler process up with %d loop(s)", len(tasks))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):  # e.g. non-main thread on some OSes
            loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    logger.info("scheduler process shutting down")
    await stop_storefront_schedulers(tasks)

    # Release the reused APNs HTTP/2 client cleanly — the loops use it to push to
    # the registers, exactly as the HTTP app does (F-POS-12).
    from app.services.providers import apns_provider

    with suppress(Exception):
        await apns_provider.provider.aclose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
