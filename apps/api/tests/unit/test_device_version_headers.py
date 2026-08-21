"""
A terminal reports what it is running, and nothing about that can hurt it.

`app_version` used to be written once by pairing and never again, so the console
reported the build an iPad was set up with rather than the one on it. It is now
refreshed from the `X-App-*` headers at `authenticate_device`, the single choke
point every device request passes through — which means the rules that matter
are about restraint, not capture: a missing header must not blank a column, a
junk one must not reach a CHECK constraint, and none of it may fail the request
that carried it.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1.devices import (
    SEEN_REFRESH_SECONDS,
    ReportedBuild,
    authenticate_device,
    hash_device_token,
)
from app.core.exceptions import UnauthorizedError
from app.models.base import utcnow
from app.models.device import Device, DeviceStatusEnum

TOKEN = "a-device-token"


def make_device(**overrides) -> Device:
    device = Device(
        name="Counter 1",
        reference="C01",
        type="cashier",
        status=DeviceStatusEnum.USED.value,
        token_hash=hash_device_token(TOKEN),
    )
    for key, value in overrides.items():
        setattr(device, key, value)
    device.id = overrides.get("id", __import__("uuid").uuid4())
    return device


def db_returning(device: Device):
    db = AsyncMock()
    result = AsyncMock()
    result.scalar_one_or_none = lambda: device
    db.execute = AsyncMock(return_value=result)
    return db


# ── Reading the headers ──────────────────────────────────────────────────────


def test_headers_are_trimmed_and_normalised():
    build = ReportedBuild.from_headers("  1.4 ", " 36 ", "IOS")
    assert build == ReportedBuild(app_version="1.4", build_number="36", platform="ios")


def test_a_platform_we_do_not_recognise_is_dropped_not_stored():
    """
    The column has a CHECK on it, and this write happens inside a best-effort
    stamp that swallows its own errors. A junk value reaching the database would
    be a constraint violation nobody ever sees.
    """
    for junk in ("windows", "iOS 17", "", "   ", "'; drop table devices; --"):
        assert ReportedBuild.from_headers(None, None, junk).platform is None


def test_overlong_headers_are_truncated_to_the_column():
    build = ReportedBuild.from_headers("v" * 200, "9" * 200, "android")
    assert len(build.app_version) == 30
    assert len(build.build_number) == 20


def test_an_absent_header_reads_as_nothing_not_as_empty():
    build = ReportedBuild.from_headers(None, None, None)
    assert build.as_updates() == {}


# ── Deciding whether it is news ──────────────────────────────────────────────


def test_a_new_build_is_news():
    device = make_device(app_version="1.3", build_number="31", platform="ios")
    assert ReportedBuild("1.3", "36", "ios").changed_from(device) is True


def test_the_same_build_is_not():
    device = make_device(app_version="1.4", build_number="36", platform="ios")
    assert ReportedBuild("1.4", "36", "ios").changed_from(device) is False


def test_a_missing_header_never_counts_as_a_change():
    """
    Otherwise a terminal that sends only some of the headers would look like it
    had changed on every single request, and write a row per tap.
    """
    device = make_device(app_version="1.4", build_number="36", platform="ios")
    assert ReportedBuild(app_version="1.4").changed_from(device) is False
    assert ReportedBuild().changed_from(device) is False


def test_a_missing_header_never_blanks_a_column():
    """The whole point of `as_updates` skipping `None`."""
    assert ReportedBuild(app_version="1.4").as_updates() == {"app_version": "1.4"}


# ── At the choke point ───────────────────────────────────────────────────────


async def test_a_changed_build_is_written_immediately_despite_the_throttle():
    """
    A build change happens once per update, not once per tap, and it is the
    moment worth catching. Waiting out the throttle would mean a terminal that
    updates and then goes quiet keeps reporting the old build.
    """
    device = make_device(
        last_seen_at=utcnow(),  # well inside SEEN_REFRESH_SECONDS
        app_version="1.3",
        build_number="31",
        platform="ios",
    )
    with patch("app.api.v1.devices._stamp_last_seen", new=AsyncMock()) as stamp:
        await authenticate_device(
            db_returning(device), TOKEN, ReportedBuild("1.4", "36", "ios")
        )
    stamp.assert_awaited_once()
    # And the in-memory row agrees with what was written, so anything else this
    # request reads off it is not looking at the old build.
    assert (device.app_version, device.build_number) == ("1.4", "36")


async def test_an_unchanged_build_inside_the_throttle_writes_nothing():
    device = make_device(
        last_seen_at=utcnow(), app_version="1.4", build_number="36", platform="ios"
    )
    with patch("app.api.v1.devices._stamp_last_seen", new=AsyncMock()) as stamp:
        await authenticate_device(
            db_returning(device), TOKEN, ReportedBuild("1.4", "36", "ios")
        )
    stamp.assert_not_awaited()


async def test_a_stale_seen_still_writes_without_any_headers():
    """The behaviour that existed before this feature, unchanged."""
    device = make_device(
        last_seen_at=utcnow() - timedelta(seconds=SEEN_REFRESH_SECONDS + 1)
    )
    with patch("app.api.v1.devices._stamp_last_seen", new=AsyncMock()) as stamp:
        await authenticate_device(db_returning(device), TOKEN, ReportedBuild())
    stamp.assert_awaited_once()


async def test_a_terminal_that_sends_no_headers_at_all_still_authenticates():
    """
    The reason this can deploy ahead of the app: every till on a build that
    predates these headers behaves exactly as it did.
    """
    device = make_device(last_seen_at=utcnow())
    with patch("app.api.v1.devices._stamp_last_seen", new=AsyncMock()):
        resolved = await authenticate_device(db_returning(device), TOKEN, None)
    assert resolved is device
    assert resolved.build_number is None


async def test_a_failing_stamp_does_not_fail_the_request():
    """A version column is not worth failing a sale over."""
    device = make_device(last_seen_at=None)
    with patch(
        "app.api.v1.devices.AsyncSessionFactory", side_effect=OSError("db gone")
    ):
        resolved = await authenticate_device(
            db_returning(device), TOKEN, ReportedBuild("1.4", "36", "ios")
        )
    assert resolved is device


async def test_a_disabled_device_is_still_refused():
    device = make_device(status=DeviceStatusEnum.DISABLED.value)
    with pytest.raises(UnauthorizedError):
        await authenticate_device(
            db_returning(device), TOKEN, ReportedBuild("1.4", "36", "ios")
        )
