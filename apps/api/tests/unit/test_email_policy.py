"""
One email policy: inline-await through the never-raise funnel, journalled,
and watched.

Two contradictory policies used to coexist — `orders.py` awaited its sends
inline because BackgroundTasks are silently dropped on serverless, while
`auth.py` used BackgroundTasks for exactly the two emails whose loss is
hardest to diagnose (a welcome nobody misses, and a password reset whose
absence looks identical to the anti-enumeration 200). These tests pin the
resolution: everything inlines, the funnel genuinely never raises so inlining
is safe, and the journal's failures are shouted about rather than left in a
table nobody reads.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.api.v1.auth
from app.services import email_service


# ─── The policy is actually applied at the call sites ─────────────────────────


def test_auth_no_longer_uses_background_tasks():
    """
    The whole point of the policy is that there is one way to send. A
    BackgroundTasks import creeping back into auth.py is the regression this
    guards against — it compiles, it works on a laptop, and it drops email
    on the deployment target.
    """
    import app.api.v1.auth as auth

    source = inspect.getsource(auth)
    assert "BackgroundTasks" not in source
    assert "background_tasks" not in source


def test_no_router_uses_background_tasks_for_anything():
    """
    The policy is about the mechanism, not about email.

    Guarding `auth.py` alone left three routers — `uploads`, `import_data` and
    `cms` — using `BackgroundTasks` for CDN warming, with no comment
    acknowledging the deviation, while `indexnow_service` solved the identical
    "do not make the admin wait for this" problem with a tracked
    `asyncio.Task`. Two shapes for one problem is what convention 5 exists to
    stop, and the failure mode does not care what the payload was: the process
    can be reaped the moment the response goes out, and the work is dropped
    with nothing recording it.
    """
    api_dir = Path(inspect.getfile(app.api.v1.auth)).parent
    offenders = sorted(
        path.name
        for path in api_dir.rglob("*.py")
        if "BackgroundTasks" in path.read_text(encoding="utf-8")
    )

    assert not offenders, (
        "these routers use BackgroundTasks, which is dropped when the serving "
        "process is reaped. For work that must not block the response, use a "
        "tracked asyncio.Task the way `indexnow_service.submit_in_background` "
        f"and `image_warm_service.warm_in_background` do:\n  {chr(10).join(offenders)}"
    )


def test_the_two_auth_emails_are_awaited_inline():
    import app.api.v1.auth as auth

    assert "await email_service.send_welcome(" in inspect.getsource(auth.register)
    assert "await email_service.send_password_reset(" in inspect.getsource(
        auth.forgot_password
    )


# ─── Inlining is safe because the funnel never raises ─────────────────────────


@pytest.mark.asyncio
async def test_a_broken_welcome_template_is_a_failed_email_not_a_500(monkeypatch):
    """
    Under BackgroundTasks a render error detonated after the response, where
    nobody saw it. Inline, the same error would 500 a registration whose
    account row already exists — unless the funnel eats it and journals a
    failed send instead. This is the guarantee that makes inlining safe to
    mandate.
    """
    logged = []

    def _boom(*_a, **_k):
        raise RuntimeError("template typo")

    async def _capture_log(template, recipient, subject, result, order_number=None):
        logged.append(result)

    monkeypatch.setattr(email_service, "_render", _boom)
    monkeypatch.setattr(email_service, "_log", _capture_log)

    await email_service.send_welcome("customer@example.com")

    assert logged and logged[0]["status"] == "failed"
    assert "template typo" in logged[0]["error"]


@pytest.mark.asyncio
async def test_a_broken_reset_template_is_a_failed_email_not_a_500(monkeypatch):
    logged = []

    def _boom(*_a, **_k):
        raise RuntimeError("template typo")

    async def _capture_log(template, recipient, subject, result, order_number=None):
        logged.append((template, result))

    monkeypatch.setattr(email_service, "_render", _boom)
    monkeypatch.setattr(email_service, "_log", _capture_log)

    await email_service.send_password_reset("customer@example.com", "tok")

    assert logged and logged[0][0] == "password_reset"
    assert logged[0][1]["status"] == "failed"


# ─── The journal is watched ───────────────────────────────────────────────────


def _db_returning(rows):
    result = MagicMock()
    result.all.return_value = rows
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_failed_sends_in_the_last_day_are_shouted_about(caplog):
    db = _db_returning([("order_confirmation", 3), ("password_reset", 1)])

    with caplog.at_level(logging.ERROR, logger=email_service.__name__):
        total = await email_service.report_failed_sends(
            db, datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
        )

    assert total == 4
    shout = caplog.records[-1].getMessage()
    assert "4 email send(s) failed" in shout
    # Named per template, because "4 failed" sends a person querying the table
    # while "password_reset: 1" tells them a customer is locked out right now.
    assert "order_confirmation: 3" in shout
    assert "password_reset: 1" in shout


@pytest.mark.asyncio
async def test_a_clean_day_stays_quiet(caplog):
    """An hourly error line with nothing behind it teaches people to ignore
    the channel that exists to be believed."""
    db = _db_returning([])

    with caplog.at_level(logging.ERROR, logger=email_service.__name__):
        total = await email_service.report_failed_sends(db)

    assert total == 0
    assert caplog.records == []


def test_the_watch_rides_the_retention_sweep():
    """
    The sweep already holds the one-worker advisory lock and a session over
    `email_logs` every hour — the watch rides it so the whole deployment
    shouts once, not once per worker. If it leaves the sweep it needs its own
    lock, which is what this assertion will make whoever moves it discover.
    """
    from app.services import log_retention

    assert "report_failed_sends" in inspect.getsource(log_retention.sweep_once)


def test_the_watch_alerts_rather_than_resending():
    """
    Deliberate, and cheap to re-litigate by accident: `email_logs` keeps no
    payload (a reset token is minted per-request and never stored) and no
    attempts column, so a resender would loop on permanent failures and race
    the status update into double sends. Monitoring is the safe 80%. The full
    reasoning lives on `report_failed_sends` — this pins that the function
    only reads.
    """
    source = inspect.getsource(email_service.report_failed_sends)
    for verb in ("_send(", "resend.Emails", "add(", "update(", "delete("):
        assert verb not in source


def test_failed_rows_from_before_the_window_are_not_recounted():
    """The cutoff is what keeps one bad afternoon from alerting for a week."""
    source = inspect.getsource(email_service.report_failed_sends)
    assert "timedelta(days=1)" in source
