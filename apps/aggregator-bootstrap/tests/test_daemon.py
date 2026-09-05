"""The daemon's pure logic: Dubai wall-clock scheduling, the timeout guard,
job→coroutine routing, and dead-session escalation. No real browser is opened —
`_dispatch` and the browser/reauth calls are mocked."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

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
        q, Job(kind=JobKind.KEETA_ORDERS, seq=0, channel="keeta")
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


async def test_run_job_guarded_chains_keeta_orders_after_a_fresh_relogin(
    monkeypatch, tmp_path
):
    """Keeta's merchant session dies fast, so the 3-hourly pull hydrates a dead one.
    A successful keeta RELOGIN must pull orders immediately, while it is fresh."""
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))

    async def _ok(_job):
        return reauth.ReloginOutcome.OK

    monkeypatch.setattr(daemon, "_dispatch", _ok)
    monkeypatch.setattr(daemon, "_timeout_for", lambda kind: 30)

    q = JobQueue()
    await daemon.run_job_guarded(q, Job(kind=JobKind.RELOGIN, seq=0, channel="keeta"))
    assert (JobKind.KEETA_ORDERS, "keeta") in q.pending()


async def test_run_job_guarded_does_not_chain_for_other_channels(monkeypatch, tmp_path):
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))

    async def _ok(_job):
        return reauth.ReloginOutcome.OK

    monkeypatch.setattr(daemon, "_dispatch", _ok)
    monkeypatch.setattr(daemon, "_timeout_for", lambda kind: 30)

    q = JobQueue()
    await daemon.run_job_guarded(q, Job(kind=JobKind.RELOGIN, seq=0, channel="noon"))
    assert q.pending() == set()  # only keeta chains an orders pull


async def test_run_job_guarded_does_not_reloop_relogin_when_in_cooldown(
    monkeypatch, tmp_path
):
    """A job that keeps failing signed-out (a keeta session that died within seconds
    of its relogin) must NOT re-drive a headed relogin while the success floor holds
    — otherwise it tight-loops headed Chrome and pegs the box's CPU."""
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(reauth.settings, "WORKER_MIN_RELOGIN_INTERVAL_SECONDS", 900)
    reauth._record_relogin_success("keeta")  # a relogin just succeeded

    async def _dead(_job):
        raise NeedsHumanLogin("signed out again within seconds")

    monkeypatch.setattr(daemon, "_dispatch", _dead)
    monkeypatch.setattr(daemon, "_timeout_for", lambda kind: 30)

    q = JobQueue()
    await daemon.run_job_guarded(
        q, Job(kind=JobKind.KEETA_ORDERS, seq=0, channel="keeta")
    )
    assert q.pending() == set()  # in cooldown → no relogin, no loop


async def test_run_job_guarded_does_not_relogin_server_refreshable_on_failure(
    monkeypatch,
):
    """A failed Deliveroo menu capture used to enqueue a headed RELOGIN even
    though the API remints the token itself. Skip on job failure, not only heal."""

    async def _dead(_job):
        raise NeedsHumanLogin("menu capture signed out")

    monkeypatch.setattr(daemon, "_dispatch", _dead)
    monkeypatch.setattr(daemon, "_timeout_for", lambda kind: 30)

    q = JobQueue()
    await daemon.run_job_guarded(
        q, Job(kind=JobKind.DELIVEROO_MENU, seq=0, channel="deliveroo")
    )
    assert q.pending() == set()  # server_refreshable → no headed relogin


async def test_run_job_guarded_still_escalates_non_refreshable_channels(
    monkeypatch,
):
    """Talabat is headed-only; a dead session on job failure still enqueues RELOGIN."""

    async def _dead(_job):
        raise NeedsHumanLogin("stored state no longer authenticates")

    monkeypatch.setattr(daemon, "_dispatch", _dead)
    monkeypatch.setattr(daemon, "_timeout_for", lambda kind: 30)

    q = JobQueue()
    await daemon.run_job_guarded(
        q, Job(kind=JobKind.DELIVEROO_MENU, seq=0, channel="talabat")
    )
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

    async def _keeta_orders():
        seen.append(("keeta_orders", None))

    async def _deliveroo():
        seen.append(("deliveroo_finance", None))

    async def _keeta_finance():
        seen.append(("keeta_finance", None))

    async def _keeta_hours(**kwargs):
        seen.append(("keeta_hours", kwargs.get("persist")))

    monkeypatch.setattr(warm, "warm_channel", _warm)
    monkeypatch.setattr(warm, "warm_keeta_orders", _keeta_orders)
    monkeypatch.setattr(warm, "pull_deliveroo_invoices_in_page", _deliveroo)
    monkeypatch.setattr(warm, "pull_keeta_finance_in_page", _keeta_finance)
    monkeypatch.setattr(warm, "write_keeta_hours_in_page", _keeta_hours)

    await daemon._dispatch(Job(kind=JobKind.WARM, seq=0, channel="noon"))
    await daemon._dispatch(Job(kind=JobKind.KEETA_ORDERS, seq=1, channel="keeta"))
    await daemon._dispatch(Job(kind=JobKind.KEETA_FINANCE, seq=2, channel="keeta"))
    await daemon._dispatch(
        Job(kind=JobKind.DELIVEROO_FINANCE, seq=3, channel="deliveroo")
    )
    await daemon._dispatch(Job(kind=JobKind.KEETA_HOURS, seq=4, channel="keeta"))

    assert seen == [
        ("warm", "noon"),
        ("keeta_orders", None),  # KEETA_ORDERS = session refresh + orders (no finance)
        ("keeta_finance", None),  # KEETA_FINANCE is the separate nightly finance pull
        ("deliveroo_finance", None),
        ("keeta_hours", True),
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


def test_daily_jobs_schedules_keeta_hours_after_finance(monkeypatch):
    """KEETA_HOURS is a daily job at 05:00 DXB, after KEETA_FINANCE at 04:00."""
    monkeypatch.setattr(daemon, "_jitter", lambda: timedelta(0))
    monkeypatch.setattr(daemon, "_warm_channels", lambda: [])
    # 20:00 UTC = 00:00 Dubai — both 04:00 and 05:00 DXB are still ahead today.
    now = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)
    jobs = daemon._daily_jobs(now)
    by_kind = {j.kind: j for j in jobs}
    assert JobKind.KEETA_HOURS in by_kind
    hours = by_kind[JobKind.KEETA_HOURS]
    finance = by_kind[JobKind.KEETA_FINANCE]
    assert hours.channel == "keeta"
    assert hours.hour == 5
    assert finance.hour == 4
    # 04:00 DXB = 00:00 UTC next day; 05:00 DXB = 01:00 UTC next day.
    assert finance.next_at == datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)
    assert hours.next_at == datetime(2026, 1, 2, 1, 0, tzinfo=timezone.utc)
    assert hours.next_at > finance.next_at
