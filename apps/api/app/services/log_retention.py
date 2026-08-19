"""
Throwing away the logs once they stop being able to tell anyone anything.

Three tables grow forever and only one of them is worth keeping forever.

* **`webhook_logs`** is the fastest of them by an order of magnitude — noon Send
  push a rider position every 15-30 seconds per live task, and every one is
  stored at full payload. That completeness is only affordable because of this
  file.
* **`email_logs`** is one row per send, which is slow, but the value of a row is
  "did this customer get their email today". A month-old send is noise.
* **`webhook_events`** is a dedup ledger. A row older than the window in which a
  courier might replay an event is doing nothing at all.
* **`audit_logs`** is different in kind, and kept far longer. It is not
  debugging output — it is the record of who changed what, and the moment it is
  wanted is a dispute weeks after the change. Seven days would mean a question
  raised a fortnight later has no answer, so it gets `AUDIT_RETENTION_DAYS`.

The sweep borrows `batch_scheduler`'s shape exactly — an advisory lock so only
one worker in the deployment is ever inside one, and a loop in the app's own
lifespan, because this stack has no cron and nothing that survives the
container. It ticks hourly rather than by the minute: the point is that the
tables stay bounded, and being an hour late to delete a row costs nothing.

It also carries the failed-email watch. `email_logs` journals every send, but a
journal nobody reads is where a Resend outage goes to be permanent — so while
this sweep is already holding the lock and a session over that table, it asks
`email_service.report_failed_sends` to shout about the last day's failures.
Riding this loop rather than owning one keeps it to exactly one alert an hour
across the whole deployment. Why it alerts instead of resending is documented
on the function itself.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.core.config import settings
from app.core import advisory_lock
from app.core.database import AsyncSessionFactory
from app.models.audit_log import AuditLog
from app.models.email_log import EmailLog
from app.models.webhook_event import WebhookEvent
from app.models.webhook_log import WebhookLog
from app.services import email_service

logger = logging.getLogger(__name__)

__all__ = ["run_forever", "sweep_once"]

#: Same flat 64-bit namespace as every other advisory lock in the database, so
#: this has to be a number nothing else picks. "mmBATCH" + 2.
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_4802

_TICK_SECONDS = 3600


def _cutoff(days: int, now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)) - timedelta(days=max(1, days))


async def sweep_once(now: datetime | None = None) -> dict[str, int]:
    """
    One pass: claim the lock, delete what has aged out, let go.

    Returns how many rows went, per table — `{}` when another worker held the
    lock, which is a normal outcome rather than a failure.
    """
    async with advisory_lock.held(_ADVISORY_LOCK_KEY, name="log retention") as mine:
        if not mine:
            return {}
        async with AsyncSessionFactory() as session:
            short = _cutoff(settings.LOG_RETENTION_DAYS, now)
            long = _cutoff(settings.AUDIT_RETENTION_DAYS, now)

            removed = {
                "webhook_logs": await _purge(
                    session, WebhookLog, WebhookLog.received_at, short
                ),
                "email_logs": await _purge(session, EmailLog, EmailLog.sent_at, short),
                "webhook_events": await _purge(
                    session, WebhookEvent, WebhookEvent.processed_at, short
                ),
                "audit_logs": await _purge(
                    session, AuditLog, AuditLog.created_at, long
                ),
            }
            await session.commit()
            # Under the same lock, so one worker shouts once an hour rather
            # than every worker shouting at once. Failure to count must not
            # break the purge that just committed — the retention guarantee
            # and the alerting are independent jobs sharing a ride.
            try:
                await email_service.report_failed_sends(session, now)
            except Exception:  # noqa: BLE001 — the watch must not kill the sweep
                logger.exception("Failed-email watch errored; purge unaffected")
            return {table: count for table, count in removed.items() if count}


async def _purge(session, model, column, cutoff: datetime) -> int:
    """Delete every row of `model` whose `column` is older than `cutoff`."""
    result = await session.execute(delete(model).where(column < cutoff))
    return result.rowcount or 0


async def run_forever() -> None:
    """
    The loop. Cancelled on shutdown; never allowed to die on an exception.

    A sweep that raises must be survivable — one bad delete must not stop every
    future one, because the failure mode of not sweeping is a table that grows
    until it is somebody's Saturday.
    """
    logger.info(
        "Log retention started (every %ss; logs %s days, audit %s days)",
        _TICK_SECONDS,
        settings.LOG_RETENTION_DAYS,
        settings.AUDIT_RETENTION_DAYS,
    )
    while True:
        try:
            # Sleeps first. Boot is the busiest moment a process has, and
            # nothing here is urgent enough to compete with serving requests.
            await asyncio.sleep(_TICK_SECONDS)
            removed = await sweep_once()
            if removed:
                logger.info(
                    "Log retention removed %s",
                    ", ".join(
                        f"{count} from {table}" for table, count in removed.items()
                    ),
                )
        except asyncio.CancelledError:
            logger.info("Log retention stopping")
            raise
        except Exception:  # noqa: BLE001 — the loop outlives any one failure
            logger.exception("Log retention sweep failed; retrying on the next tick")
