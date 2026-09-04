"""The always-on worker daemon: one priority queue, one browser at a time.

This replaces the three host-cron one-shot containers (keeta pull, nightly warm,
2-minute heal) and their single shared `flock`. It is a long-lived process that
runs under a RESIDENT Xvfb (see docker-entrypoint.sh) but keeps NO browser
resident: a scheduler coroutine enqueues jobs on cadence, and a SINGLE consumer
drains the queue one job at a time, spawning Chrome for that job and tearing it
down after. One consumer ⇒ at most one Chrome ever ⇒ the RAM guarantee on the
e2-small, and job priority means a RELOGIN preempts a queued cookie WARM.

Every job runs under a hard `asyncio.wait_for` budget: a wedged Chrome (a stuck
OTP wait, a hung page.goto) is SIGKILLed via `browser.kill_live_chrome()` — the
in-process equivalent of the cron's `timeout -k 30 <budget>` — and the consumer
moves on. Nothing a job does can kill the consumer.

Cadences, timeouts and poll intervals are `config.Settings` fields (worker
tunables, defaults reproduce the retired cron timings); there are no bare magic
numbers here.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import browser, observability, push, reauth, warm
from .browser import NeedsHumanLogin, NotLoggedInError
from .channels.probes import CHANNEL_PROBES
from .config import settings
from .queue import Job, JobKind, JobQueue

logger = logging.getLogger("aggregator-bootstrap")

#: Asia/Dubai is a permanent UTC+4 (no daylight saving since 1972), so we add a
#: fixed offset instead of depending on tzdata being present in the slim image.
_DXB_UTC_OFFSET = timedelta(hours=4)

#: JobKind → the Settings field holding its hard per-job timeout budget.
_TIMEOUT_FIELDS: dict[JobKind, str] = {
    JobKind.RELOGIN: "WORKER_RELOGIN_TIMEOUT_SECONDS",
    JobKind.WARM: "WORKER_WARM_TIMEOUT_SECONDS",
    JobKind.KEETA_ORDERS: "WORKER_KEETA_ORDERS_TIMEOUT_SECONDS",
    JobKind.KEETA_FINANCE: "WORKER_KEETA_FINANCE_TIMEOUT_SECONDS",
    JobKind.DELIVEROO_FINANCE: "WORKER_DELIVEROO_FINANCE_TIMEOUT_SECONDS",
    JobKind.KEETA_MENU: "WORKER_KEETA_MENU_TIMEOUT_SECONDS",
    JobKind.DELIVEROO_MENU: "WORKER_DELIVEROO_MENU_TIMEOUT_SECONDS",
}


def next_daily_dxb(hour: int, now_utc: datetime) -> datetime:
    """The next UTC instant at which the Dubai wall-clock reads `hour`:00.

    Kept as a pure function of `now_utc` so the scheduler stays testable. Returns a
    tz-aware UTC datetime strictly in the future (if `hour` has already passed today
    in Dubai, it rolls to tomorrow). This is how the nightly anti-bot warm stays
    aligned with the API's 23:00 Dubai (`AGGREGATOR_RUN_HOUR_DXB`) sweep window.
    """
    dxb_now = now_utc.astimezone(timezone.utc) + _DXB_UTC_OFFSET
    target = dxb_now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= dxb_now:
        target += timedelta(days=1)
    return target - _DXB_UTC_OFFSET


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _jitter() -> timedelta:
    """A random 0..`WORKER_JITTER_SECONDS` spread (zero when the setting is <= 0).

    Added to every scheduled fire so jobs pinned to the same wall-clock hour (the
    two nightly warms) and boot-relative interval jobs that would re-align on a
    restart do not arrive at the single consumer in a burst.
    """
    span = settings.WORKER_JITTER_SECONDS
    if span <= 0:
        return timedelta(0)
    return timedelta(seconds=random.uniform(0, span))


def _touch_heartbeat() -> None:
    """Bump the heartbeat file the compose healthcheck watches.

    Touched every scheduler tick (and after every job), so a daemon whose event
    loop is alive but idle still looks healthy, while a wedged loop goes stale.
    """
    path = Path(settings.WORKER_HEARTBEAT_PATH)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except OSError:  # pragma: no cover — a heartbeat write failure is non-fatal
        logger.warning("daemon: could not touch heartbeat %s", path)


def _warm_channels() -> list[str]:
    """The channels to warm nightly, parsed from WORKER_WARM_CHANNELS."""
    out: list[str] = []
    for raw in settings.WORKER_WARM_CHANNELS.split(","):
        ch = raw.strip()
        if not ch:
            continue
        if ch not in CHANNEL_PROBES:
            logger.warning("daemon: ignoring unknown warm channel %r", ch)
            continue
        out.append(ch)
    return out


def _timeout_for(kind: JobKind) -> int:
    return int(getattr(settings, _TIMEOUT_FIELDS[kind]))


async def _dispatch(job: Job) -> None:
    """Map a job to the existing coroutine that does the work — reuse, not reimpl.

    RELOGIN drives the stored login in a worker thread: `reauth._try_auto_relogin`
    is synchronous and starts its own event loop (`asyncio.run`), which cannot run
    inside the daemon's loop, so `to_thread` gives it one. It never raises; its
    outcome decides whether the channel's backoff is cleared or armed.
    """
    if job.kind is JobKind.RELOGIN:
        outcome = await asyncio.to_thread(reauth._try_auto_relogin, job.channel)
        if outcome is reauth.ReloginOutcome.OK:
            # Stamp the success so `_heal_poll` will not re-drive a healthy-but-
            # short-lived session (Talabat's rotating cookie) again for
            # WORKER_MIN_RELOGIN_INTERVAL_SECONDS — the headed-Chrome CPU floor.
            await asyncio.to_thread(reauth._record_relogin_success, job.channel)
            await asyncio.to_thread(reauth._clear_reauth_backoff, job.channel)
        else:
            await asyncio.to_thread(
                reauth._record_reauth_failure,
                job.channel,
                transient=(outcome is reauth.ReloginOutcome.TRANSIENT),
            )
        return
    if job.kind is JobKind.WARM:
        await warm.warm_channel(job.channel)
        return
    if job.kind is JobKind.KEETA_ORDERS:
        # Refresh the keeta session cookies + pull orders in-page (Keeta is
        # mtgsig-signed in-page, the one channel the API's httpx sweep cannot reach).
        # Finance is a separate nightly KEETA_FINANCE job so its slow downloads never
        # block a heal/orders behind them on the single consumer. (This is
        # warm_keeta_orders, NOT warm_channel("keeta") — the latter also pulls finance
        # and is the manual/CLI full pull.)
        await warm.warm_keeta_orders()
        return
    if job.kind is JobKind.KEETA_FINANCE:
        await warm.pull_keeta_finance_in_page()
        return
    if job.kind is JobKind.KEETA_MENU:
        # Read the Keeta menu in-page (mtgsig) and push it for the catalog sync.
        await warm.pull_keeta_menu_in_page()
        return
    if job.kind is JobKind.DELIVEROO_FINANCE:
        await warm.pull_deliveroo_invoices_in_page()
        return
    if job.kind is JobKind.DELIVEROO_MENU:
        # Capture the Deliveroo menu + hours in-page and push them for catalog sync.
        await warm.pull_deliveroo_menu_hours_in_page()
        return
    raise ValueError(f"unknown job kind {job.kind!r}")  # pragma: no cover


async def run_job_guarded(queue: JobQueue, job: Job) -> None:
    """Run one job under its hard timeout. Nothing here escapes to the consumer.

    On timeout: SIGKILL the wedged Chrome and park the channel on the SHORT
    (transient) backoff — a wedge is not a human-only wall. On a dead stored
    session (`NeedsHumanLogin`/`NotLoggedInError` from a warm/pull): escalate to a
    RELOGIN, which preempts the queue, mirroring the old warm cron's --auto-relogin.
    Any other error is logged and swallowed.
    """
    budget = _timeout_for(job.kind)
    label = f"{job.kind.name}{('/' + job.channel) if job.channel else ''}"
    logger.info("daemon: running %s (budget %ss)", label, budget)
    try:
        await asyncio.wait_for(_dispatch(job), timeout=budget)
    except (asyncio.TimeoutError, TimeoutError):
        logger.error("daemon: %s exceeded %ss — killing Chrome", label, budget)
        observability.note_job_timeout(job.kind.name, job.channel, budget)
        browser.kill_live_chrome()
        if job.channel:
            await asyncio.to_thread(
                reauth._record_reauth_failure, job.channel, transient=True
            )
    except (NeedsHumanLogin, NotLoggedInError) as exc:
        if job.kind is not JobKind.RELOGIN and job.channel:
            logger.warning(
                "daemon: %s stored session is dead (%s) — enqueuing RELOGIN",
                label,
                exc,
            )
            await queue.put(JobKind.RELOGIN, job.channel)
        else:
            logger.warning("daemon: %s needs a human login: %s", label, exc)
    except Exception as exc:  # noqa: BLE001 — one job must never kill the consumer
        logger.exception("daemon: %s failed", label)
        observability.capture_exception(
            exc, tags={"kind": job.kind.name, "channel": job.channel or "-"}
        )
    else:
        logger.info("daemon: finished %s", label)


async def _heal_poll(queue: JobQueue) -> None:
    """Ask the API which sessions are dead and enqueue a RELOGIN for each.

    Reuses `push.pull_sessions` + `reauth._channel_needs_reauth` so the daemon and
    the ingest agree on "dead", and honours the per-channel reauth backoff so a
    human-only channel is not re-driven every poll. A channel that went healthy on
    its own has any standing backoff cleared, exactly as the one-shot heal did.
    """
    try:
        bundles = await push.pull_sessions()
    except Exception:  # noqa: BLE001 — a transient API blip must not kill the loop
        logger.exception("daemon: heal poll could not read session health")
        return
    for bundle in bundles:
        ch = bundle.get("channel")
        if not ch:
            continue
        if reauth._channel_needs_reauth(bundle) is None:
            await asyncio.to_thread(reauth._clear_reauth_backoff, ch)
            continue
        if bundle.get("server_refreshable"):
            # The API renews this channel itself over httpx (Deliveroo re-mints its
            # token before every sweep). A headed RELOGIN here would burn a Chrome
            # on this e2-small doing what the next API sweep does for free.
            continue
        if reauth._reauth_cooldown_remaining(ch) > 0:
            continue
        # A channel that re-logged in successfully within the floor is not re-driven
        # again yet, even if it looks dead — this is what stops Talabat's hourly
        # cookie rotation from spawning a headed Chrome on every 2-minute heal poll.
        if reauth._success_cooldown_remaining(ch) > 0:
            continue
        if await queue.put(JobKind.RELOGIN, ch):
            logger.info("daemon: %s is dead — enqueued RELOGIN", ch)


@dataclass
class _Daily:
    """A once-a-day job pinned to a Dubai wall-clock hour."""

    next_at: datetime
    kind: JobKind
    channel: str
    hour: int


async def _run_scheduler(queue: JobQueue) -> None:
    """Enqueue jobs on their cadence. Idempotent: the queue dedupes, so re-enqueuing
    a still-pending job is a no-op — the scheduler never has to track in-flight state
    beyond its own next-due timers."""
    now = _now_utc()
    daily: list[_Daily] = [
        _Daily(
            next_at=next_daily_dxb(settings.WORKER_WARM_HOUR_DXB, now) + _jitter(),
            kind=JobKind.WARM,
            channel=ch,
            hour=settings.WORKER_WARM_HOUR_DXB,
        )
        for ch in _warm_channels()
    ]
    daily.append(
        _Daily(
            next_at=next_daily_dxb(settings.WORKER_DELIVEROO_FINANCE_HOUR_DXB, now)
            + _jitter(),
            kind=JobKind.DELIVEROO_FINANCE,
            channel="deliveroo",
            hour=settings.WORKER_DELIVEROO_FINANCE_HOUR_DXB,
        )
    )
    # Keeta FINANCE is nightly (settled files are slow to re-download); Keeta ORDERS
    # is the frequent interval job below (capture-before-masking). Splitting them
    # keeps the long finance download off the every-few-hours hot path.
    daily.append(
        _Daily(
            next_at=next_daily_dxb(settings.WORKER_KEETA_FINANCE_HOUR_DXB, now)
            + _jitter(),
            kind=JobKind.KEETA_FINANCE,
            channel="keeta",
            hour=settings.WORKER_KEETA_FINANCE_HOUR_DXB,
        )
    )
    # Interval jobs fire immediately on start, so a fresh deploy pulls Keeta orders
    # and heals dead sessions at once rather than waiting a full cadence.
    keeta_delta = timedelta(hours=settings.WORKER_KEETA_PULL_INTERVAL_HOURS)
    heal_delta = timedelta(seconds=settings.WORKER_HEAL_POLL_SECONDS)
    keeta_next = now
    heal_next = now
    #: Keeta MENU cadence — disabled when the interval is <= 0.
    menu_hours = settings.WORKER_KEETA_MENU_INTERVAL_HOURS
    menu_delta = timedelta(hours=menu_hours) if menu_hours > 0 else None
    menu_next = now
    #: Deliveroo MENU+HOURS cadence — disabled when the interval is <= 0.
    del_menu_hours = settings.WORKER_DELIVEROO_MENU_INTERVAL_HOURS
    del_menu_delta = timedelta(hours=del_menu_hours) if del_menu_hours > 0 else None
    del_menu_next = now
    tick = max(settings.WORKER_SCHEDULER_TICK_SECONDS, 1)

    logger.info(
        "daemon: scheduler up — warm=%s@%02d:00 DXB, keeta orders every %sh, "
        "keeta finance @%02d:00 DXB, heal every %ss",
        [d.channel for d in daily if d.kind is JobKind.WARM],
        settings.WORKER_WARM_HOUR_DXB,
        settings.WORKER_KEETA_PULL_INTERVAL_HOURS,
        settings.WORKER_KEETA_FINANCE_HOUR_DXB,
        settings.WORKER_HEAL_POLL_SECONDS,
    )
    while True:
        now = _now_utc()
        _touch_heartbeat()
        for entry in daily:
            if now >= entry.next_at:
                await queue.put(entry.kind, entry.channel)
                entry.next_at = next_daily_dxb(entry.hour, now) + _jitter()
        if now >= keeta_next:
            await queue.put(JobKind.KEETA_ORDERS, "keeta")
            keeta_next = now + keeta_delta + _jitter()
        if menu_delta is not None and now >= menu_next:
            await queue.put(JobKind.KEETA_MENU, "keeta")
            menu_next = now + menu_delta + _jitter()
        if del_menu_delta is not None and now >= del_menu_next:
            await queue.put(JobKind.DELIVEROO_MENU, "deliveroo")
            del_menu_next = now + del_menu_delta + _jitter()
        if now >= heal_next:
            await _heal_poll(queue)
            heal_next = now + heal_delta
        await asyncio.sleep(tick)


async def _run_consumer(queue: JobQueue) -> None:
    """The single browser worker: one job at a time, forever."""
    while True:
        job = await queue.get()
        try:
            await run_job_guarded(queue, job)
        finally:
            queue.complete(job)
        _touch_heartbeat()


async def run_daemon() -> None:
    """Run the scheduler and the single consumer until the process is stopped."""
    queue = JobQueue()
    logger.info("aggregator worker daemon starting")
    _touch_heartbeat()
    await asyncio.gather(_run_consumer(queue), _run_scheduler(queue))
