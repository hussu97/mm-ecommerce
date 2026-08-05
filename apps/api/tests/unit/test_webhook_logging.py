"""
What a courier sent, and how long we keep it.

The case for the table: on 2026-08-05, MM-20260805-007 received four noon Send
pushes and MM-20260805-006 received one. Whether the missing three were never
sent, sent somewhere else, or accepted and dropped is unanswerable, because the
only evidence was container stdout and a restart had taken it. noon Send neither
retry nor keep anything to replay from.

Two properties are load-bearing and are what this file guards. The recorder must
write down a request whatever happened to it — including the ones that failed,
which are the interesting ones — and it must never itself be able to break a
webhook, because a courier that gets a 500 either retries until it disables the
URL (Lalamove) or never mentions it again (noon Send).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.webhook_log_service import Recorder, _jsonable


NOW = datetime(2026, 8, 5, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def saved(monkeypatch):
    """Capture the rows a recorder would write, without a database."""
    rows: list[dict] = []

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def add(self, row):
            rows.append(row)

        async def commit(self):
            return None

    monkeypatch.setattr(
        "app.services.webhook_log_service.AsyncSessionFactory", lambda: _Session()
    )
    return rows


def _request(host: str = "34.18.98.2"):
    return SimpleNamespace(client=SimpleNamespace(host=host))


# ── what gets written down ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_push_that_moved_an_order_is_recorded(saved):
    recorder = Recorder("noon_send", "status", request=_request())
    recorder.payload = {"order_nr": "HG85NNJRJYC4A7EI", "status_code": "delivered"}
    recorder.event_type = "delivered"
    recorder.courier_order_id = "HG85NNJRJYC4A7EI"
    recorder.finish(result={"received": True, "matched": True})
    await recorder.save()

    (row,) = saved
    assert row.provider == "noon_send"
    assert row.endpoint == "status"
    assert row.matched is True
    assert row.remote_ip == "34.18.98.2"
    assert row.payload["status_code"] == "delivered"
    assert row.error is None


@pytest.mark.asyncio
async def test_a_push_that_matched_nothing_is_recorded_too(saved):
    """
    The whole point. A run of unmatched pushes is either their bug or ours, and
    until this table existed it was invisible.
    """
    recorder = Recorder("noon_send", "tracking", request=_request())
    recorder.finish(result={"received": True, "matched": False})
    await recorder.save()

    (row,) = saved
    assert row.matched is False


@pytest.mark.asyncio
async def test_a_handler_that_blew_up_is_recorded(saved):
    recorder = Recorder("lalamove", "status", request=_request())
    recorder.finish(
        result={"received": True, "error": "bad signature"}, error="bad signature"
    )
    recorder.signature_valid = False
    await recorder.save()

    (row,) = saved
    assert row.error == "bad signature"
    assert row.signature_valid is False


@pytest.mark.asyncio
async def test_the_key_is_never_stored_only_its_fingerprint(saved):
    """
    A shared secret in a table the admin UI renders is a shared secret that
    leaks. Four characters either end is enough to tell two keys apart across
    two systems, which is the only question anybody asks of it.
    """
    secret = "ve-6rJklKuX8CmPz2VC2IVWyzvopbDV6YVG0PY4dwBA"
    recorder = Recorder(
        "noon_send", "status", request=_request(), key_fingerprint="ve-6…dwBA (len=43)"
    )
    recorder.finish(result={"received": True})
    await recorder.save()

    (row,) = saved
    assert row.api_key_fingerprint == "ve-6…dwBA (len=43)"
    assert secret not in str(row.api_key_fingerprint)


@pytest.mark.asyncio
async def test_a_body_that_is_not_an_object_is_kept_anyway(saved):
    """An empty connection test is still evidence that the URL was called."""
    recorder = Recorder("lalamove", "status", request=_request())
    recorder.payload = "not json at all"
    recorder.finish(result={"received": True})
    await recorder.save()

    (row,) = saved
    assert row.payload == {"raw": "not json at all"}


def test_jsonable_passes_real_json_through_untouched():
    assert _jsonable({"a": 1}) == {"a": 1}
    assert _jsonable([1, 2]) == [1, 2]
    assert _jsonable(None) is None


@pytest.mark.asyncio
async def test_a_broken_database_cannot_break_a_webhook(monkeypatch, caplog):
    """
    A 500 here costs more than a missing row: Lalamove retries anything that is
    not a 200 ten times over a day and then disables the URL outright, and noon
    Send simply never mentions it again.
    """

    def explode():
        raise RuntimeError("the database is on fire")

    monkeypatch.setattr("app.services.webhook_log_service.AsyncSessionFactory", explode)
    recorder = Recorder("noon_send", "status", request=_request())
    recorder.finish(result={"received": True})

    await recorder.save()  # must not raise

    assert "Could not record" in caplog.text


@pytest.mark.asyncio
async def test_a_request_with_no_client_is_still_recorded(saved):
    recorder = Recorder("noon_send", "status", request=SimpleNamespace(client=None))
    recorder.finish(result={"received": True})
    await recorder.save()

    (row,) = saved
    assert row.remote_ip is None


@pytest.mark.asyncio
async def test_over_long_values_are_truncated_not_refused(saved):
    """A column overflow would lose the row, which is the one thing it must not do."""
    recorder = Recorder("noon_send", "status", request=_request())
    recorder.order_number = "MM-" + "9" * 200
    recorder.courier_order_id = "X" * 200
    recorder.finish(result={"received": True})
    await recorder.save()

    (row,) = saved
    assert len(row.order_number) == 30
    assert len(row.courier_order_id) == 64


# ── how long it is kept ───────────────────────────────────────────────────────


def test_the_windows_are_what_the_settings_say():
    from app.core.config import settings
    from app.services.log_retention import _cutoff

    short = _cutoff(settings.LOG_RETENTION_DAYS, NOW)
    long = _cutoff(settings.AUDIT_RETENTION_DAYS, NOW)

    assert NOW - short == timedelta(days=settings.LOG_RETENTION_DAYS)
    assert NOW - long == timedelta(days=settings.AUDIT_RETENTION_DAYS)
    # The audit trail is who-changed-what rather than debugging output, and it
    # is wanted precisely when somebody disputes a change weeks later.
    assert long < short, "the audit trail must outlive the debugging logs"


def test_a_nonsense_window_still_keeps_a_day():
    """
    Zero or negative would mean "delete everything, continuously", which is a
    misconfiguration that should cost a day of logs rather than all of them.
    """
    from app.services.log_retention import _cutoff

    assert NOW - _cutoff(0, NOW) == timedelta(days=1)
    assert NOW - _cutoff(-30, NOW) == timedelta(days=1)
