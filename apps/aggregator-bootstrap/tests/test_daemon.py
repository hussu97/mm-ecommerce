"""The daemon's pure logic: Dubai wall-clock scheduling, the timeout guard,
job→coroutine routing, and dead-session escalation. No real browser is opened —
`_dispatch` and the browser/reauth calls are mocked."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aggregator_bootstrap import daemon, reauth, warm
from aggregator_bootstrap.browser import NeedsHumanLogin
from aggregator_bootstrap.queue import Job, JobKind, JobQueue


# ── Dubai wall-clock scheduling ──────────────────────────────────────────────
def test_next_daily_dxb_later_today():
    # 10:00 UTC = 14:00 Dubai (UTC+4); the 22:00 DXB warm is still ahead today.
    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    got = daemon.next_daily_dxb(22, now)
    # 22:00 Dubai today == 18:00 UTC today.
    assert got == datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc)
    assert got > now


def test_next_daily_dxb_rolls_to_tomorrow_when_passed():
    # 19:00 UTC = 23:00 Dubai; 22:00 DXB has already passed, so it rolls a day.
    now = datetime(2026, 1, 1, 19, 0, tzinfo=timezone.utc)
    got = daemon.next_daily_dxb(22, now)
    assert got == datetime(2026, 1, 2, 18, 0, tzinfo=timezone.utc)
    assert got > now


def test_next_daily_dxb_is_aligned_to_the_hour():
    now = datetime(2026, 6, 15, 3, 27, 41, tzinfo=timezone.utc)
    got = daemon.next_daily_dxb(22, now)
    assert (got.minute, got.second, got.microsecond) == (0, 0, 0)


# ── the per-job timeout guard ────────────────────────────────────────────────
async def test_run_job_guarded_kills_chrome_and_backs_off_on_timeout(monkeypatch):
    killed = []
    backoffs = []

    async def _overrun(_job):
        await asyncio.sleep(5)  # far beyond the tiny budget below

    monkeypatch.setattr(daemon, "_dispatch", _overrun)
    monkeypatch.setattr(daemon, "_timeout_for", lambda kind: 0.01)
    monkeypatch.setattr(daemon.browser, "kill_live_chrome", lambda: killed.append(True))
    monkeypatch.setattr(
        reauth,
        "_record_reauth_failure",
        lambda ch, *, transient=False: backoffs.append((ch, transient)),
    )

    q = JobQueue()
    job = Job(kind=JobKind.WARM, seq=0, channel="noon")
    # Must NOT raise — the consumer has to survive a wedged job.
    await daemon.run_job_guarded(q, job)

    assert killed == [True]  # the wedged Chrome was force-killed
    assert backoffs == [("noon", True)]  # a wedge is transient, not human-needed


async def test_run_job_guarded_swallows_errors(monkeypatch):
    async def _boom(_job):
        raise RuntimeError("marketplace 5xx")

    monkeypatch.setattr(daemon, "_dispatch", _boom)
    monkeypatch.setattr(daemon, "_timeout_for", lambda kind: 30)

    q = JobQueue()
    # A plain error is logged and swallowed, never propagated to the consumer.
    await daemon.run_job_guarded(
        q, Job(kind=JobKind.KEETA_PULL, seq=0, channel="keeta")
    )


async def test_run_job_guarded_escalates_dead_session_to_relogin(monkeypatch):
    async def _dead(_job):
        raise NeedsHumanLogin("stored state no longer authenticates")

    monkeypatch.setattr(daemon, "_dispatch", _dead)
    monkeypatch.setattr(daemon, "_timeout_for", lambda kind: 30)

    q = JobQueue()
    await daemon.run_job_guarded(q, Job(kind=JobKind.WARM, seq=0, channel="talabat"))
    # A dead warm enqueues a RELOGIN (which preempts the queue), like the old
    # --auto-relogin cron did.
    assert (JobKind.RELOGIN, "talabat") in q.pending()


# ── job → coroutine routing ──────────────────────────────────────────────────
async def test_dispatch_relogin_clears_backoff_on_success(monkeypatch):
    calls = {}

    def _relogin(ch):
        calls["relogin"] = ch
        return reauth.ReloginOutcome.OK

    def _clear(ch):
        calls["clear"] = ch

    monkeypatch.setattr(reauth, "_try_auto_relogin", _relogin)
    monkeypatch.setattr(reauth, "_clear_reauth_backoff", _clear)

    await daemon._dispatch(Job(kind=JobKind.RELOGIN, seq=0, channel="deliveroo"))
    assert calls == {"relogin": "deliveroo", "clear": "deliveroo"}


async def test_dispatch_relogin_records_backoff_on_failure(monkeypatch):
    recorded = {}

    monkeypatch.setattr(
        reauth, "_try_auto_relogin", lambda ch: reauth.ReloginOutcome.TRANSIENT
    )
    monkeypatch.setattr(
        reauth,
        "_record_reauth_failure",
        lambda ch, *, transient=False: recorded.update(ch=ch, transient=transient),
    )

    await daemon._dispatch(Job(kind=JobKind.RELOGIN, seq=0, channel="careem"))
    assert recorded == {"ch": "careem", "transient": True}


async def test_dispatch_routes_warm_keeta_and_deliveroo(monkeypatch):
    seen = []

    async def _warm(channel):
        seen.append(("warm", channel))

    async def _deliveroo():
        seen.append(("deliveroo_finance", None))

    monkeypatch.setattr(warm, "warm_channel", _warm)
    monkeypatch.setattr(warm, "pull_deliveroo_invoices_in_page", _deliveroo)

    await daemon._dispatch(Job(kind=JobKind.WARM, seq=0, channel="noon"))
    await daemon._dispatch(Job(kind=JobKind.KEETA_PULL, seq=1, channel="keeta"))
    await daemon._dispatch(
        Job(kind=JobKind.DELIVEROO_FINANCE, seq=2, channel="deliveroo")
    )

    assert seen == [
        ("warm", "noon"),
        ("warm", "keeta"),  # KEETA_PULL runs the keeta path of warm_channel
        ("deliveroo_finance", None),
    ]


# ── heal poll ────────────────────────────────────────────────────────────────
async def test_heal_poll_enqueues_relogin_for_dead_out_of_backoff(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))

    async def _sessions():
        return [
            {"channel": "noon", "status": "live"},
            {"channel": "talabat", "status": "needs_bootstrap"},
        ]

    monkeypatch.setattr(daemon.push, "pull_sessions", _sessions)
    # Keep the healthy-channel clear offline.
    monkeypatch.setattr(reauth, "_clear_reauth_backoff", lambda ch: None)

    q = JobQueue()
    await daemon._heal_poll(q)
    assert q.pending() == {(JobKind.RELOGIN, "talabat")}  # live noon is not enqueued


async def test_heal_poll_respects_backoff(monkeypatch, tmp_path):
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))

    async def _sessions():
        return [{"channel": "talabat", "status": "needs_bootstrap"}]

    async def _noop_report(channel, backoff_until):
        return None

    monkeypatch.setattr(daemon.push, "pull_sessions", _sessions)
    monkeypatch.setattr(reauth.push, "report_reauth_backoff", _noop_report)
    # Arm a standing backoff via a thread — exactly how the daemon calls the sync,
    # asyncio.run-based helper — so its own run() does not clash with this loop.
    await asyncio.to_thread(reauth._record_reauth_failure, "talabat")

    q = JobQueue()
    await daemon._heal_poll(q)
    assert q.pending() == set()  # in backoff → not enqueued
