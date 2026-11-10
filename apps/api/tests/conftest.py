from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

TEST_SECRET = "test-secret-key-for-testing-purposes-only-xyz123"

#: Date-drift check. `MM_TEST_SHIFT_DAYS=90 pytest tests/` runs the whole suite
#: with the wall clock moved forward, so a test that only passes because a
#: hard-coded fixture date is still "recent" fails months before it would have
#: blocked a deploy. The poison-order test pinned business_date 2026-09-06, fell
#: out of promote's 30-day window on 2026-10-06, and stopped the deploy gate.
#: The weekly `date-drift.yml` workflow runs this; unset, it does nothing.
#: Postgres `now()` is NOT shifted — code must stamp times from one clock.
_clock_shift = None


#: The database this process's tests actually run against, when it is a clone.
_clone_url: str | None = None


def _maintenance_dsn(url) -> str:
    return url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )


async def _recreate_clone(url, template: str, clone: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(_maintenance_dsn(url))
    try:
        # One clone at a time: CREATE DATABASE ... TEMPLATE refuses while any
        # other session is in the template, and xdist starts every worker at
        # once.
        await conn.execute("SELECT pg_advisory_lock(hashtext('mm-test-clone'))")
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{clone}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{clone}" TEMPLATE "{template}"')
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtext('mm-test-clone'))")
    finally:
        await conn.close()


async def _drop_clone(url, clone: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(_maintenance_dsn(url))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{clone}" WITH (FORCE)')
    finally:
        await conn.close()


def _isolate_test_database() -> None:
    """Run this process's tests in a fresh copy of the migrated test database.

    `TEST_DATABASE_URL` names the *template*: a database migrated to head (CI's
    "Prepare the test database" step, or the pre-push hook) and never written
    by a test. Each run — and each xdist worker — gets `<template>_<worker>`,
    dropped and re-cloned at session start, and both `TEST_DATABASE_URL` and
    `DATABASE_URL` are pointed at it before any test module reads them, so the
    fixtures and the app's own engines (scheduler sweeps, webhook log) agree.

    Two problems this ends, both of which cost real time. The suite used to
    share one database across every run on a machine, and the integration
    tests retire rather than delete what they create (closed ledger rows and
    recipes are immutable by trigger), so it only ever grew: a developer's test
    database was found holding 190k rows / 215 MB, and tests that sum or count
    globally (the last-admin guard, VAT totals, the replenishment build) got
    slower or failed depending on what earlier runs had left behind. And with
    one shared database, parallel workers would interleave each other's rows.

    Only `TEST_DATABASE_URL` opts in. A bare `DATABASE_URL` is left alone — it
    may be a developer's real dev database, which is not ours to clone.
    """
    global _clone_url
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        return
    import asyncio

    from sqlalchemy.engine import make_url

    url = make_url(base)
    template = url.database
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    clone = f"{template}_{worker}"
    try:
        asyncio.run(_recreate_clone(url, template, clone))
    except Exception as exc:  # pragma: no cover - environment problem
        raise pytest.UsageError(
            f"could not clone the test database {template!r} into {clone!r}: "
            f"{exc}. TEST_DATABASE_URL must name a database migrated to head "
            "(`alembic upgrade head`) that nothing else is connected to."
        ) from exc
    _clone_url = url.set(database=clone).render_as_string(hide_password=False)
    os.environ["TEST_DATABASE_URL"] = _clone_url
    os.environ["DATABASE_URL"] = _clone_url


def pytest_configure(config):
    global _clock_shift
    _isolate_test_database()
    days = int(os.environ.get("MM_TEST_SHIFT_DAYS") or 0)
    if days:
        import time_machine

        dest = datetime.now(UTC) + timedelta(days=days)
        _clock_shift = time_machine.travel(dest, tick=True)
        _clock_shift.start()


def pytest_unconfigure(config):
    if _clock_shift is not None:
        _clock_shift.stop()
    # A failed run keeps its clone, so the rows a test left can be inspected;
    # the next run replaces it either way.
    passed = getattr(config, "_mm_run_passed", False)
    if _clone_url is not None and passed and not config.getoption("keep_test_db"):
        import asyncio

        from sqlalchemy.engine import make_url

        url = make_url(_clone_url)
        try:
            asyncio.run(_drop_clone(url, url.database))
        except Exception:  # pragma: no cover - best effort
            pass


def pytest_addoption(parser):
    parser.addoption(
        "--keep-test-db",
        action="store_true",
        dest="keep_test_db",
        help="keep this run's cloned test database even when every test passed",
    )


def pytest_sessionfinish(session, exitstatus):
    session.config._mm_run_passed = exitstatus == 0


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setattr(cfg.settings, "APP_ENV", "test")


@pytest.fixture(autouse=True)
def fresh_app_engine_pools():
    """Give every test empty pools on the app's own engines.

    `app.core.database.engine` and `scheduler_engine` are module-level, so
    their pools outlive a test — but every async test runs on its own event
    loop, and an asyncpg connection belongs to the loop that opened it. A test
    that reached the database through app code (an advisory lock, a sweep, the
    webhook log) left connections in the pool, and the next test to check one
    out got `RuntimeError: ... attached to a different loop` — or, depending on
    where it surfaced, a `MissingGreenlet` or a bare `DBAPIError`. Which test
    failed depended on which tests ran before it on the same process, so it
    came and went in CI with the ordering.

    `dispose(close=False)` swaps in a new pool without awaiting anything on the
    old connections (their loop may already be closed); the sockets go with
    garbage collection, which the ResourceWarning filter in pytest.ini already
    expects. A no-op for tests that never imported the app's database module.
    """
    yield
    import sys

    database = sys.modules.get("app.core.database")
    if database is not None:
        database.engine.sync_engine.dispose(close=False)
        database.scheduler_engine.sync_engine.dispose(close=False)


@pytest.fixture(autouse=True)
def reset_courier_caches():
    """
    Clear the process-level pricing/promise caches between tests.

    `lalamove_service` and `slider_service` cache quotes,
    `noon_send_service` caches the partner limits for the life of the process
    — deliberately, since they are asked once per boot in production — and
    `delivery_promise` caches the courier/branch-hours/holiday inputs a checkout
    ETA reads. In a test run "the process" is the whole suite, so one test that
    populates a cache hands it to every test that follows, and the leak surfaces
    as an order that depends on test ordering.

    Individual tests already called `clear_caches()` by hand. That works only
    for the tests that remember, which is the shape of every convention this
    repo has since turned into a fixture or a guard test.
    """
    from app.core import reference_cache
    from app.services.couriers import (
        lalamove_service,
        noon_send_service,
        slider_service,
    )
    from app.services.delivery import delivery_promise
    from app.services.pos import counter_bundle_cache

    for reset in (
        lalamove_service.clear_caches,
        slider_service.clear_caches,
        noon_send_service.invalidate_limits,
        delivery_promise.clear_caches,
        counter_bundle_cache.clear,
        reference_cache.clear,
    ):
        reset()
    yield
    for reset in (
        lalamove_service.clear_caches,
        slider_service.clear_caches,
        noon_send_service.invalidate_limits,
        delivery_promise.clear_caches,
        counter_bundle_cache.clear,
        reference_cache.clear,
    ):
        reset()


@pytest.fixture
def mock_db():
    session = AsyncMock()
    session.add = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=None)
    # Both accessors answer the same, so a test stubbing one is not silently
    # pinned to the SQLAlchemy call the code happens to use today.
    mock_result.scalars.return_value.first.return_value = None
    mock_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=mock_result)
    return session


@pytest.fixture
async def client(mock_db):
    from app.core.deps import get_db, get_db_lazy
    from app.main import app

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    # The cached public routes take the lazy session; in a test it is the same
    # mock, or a cache miss would reach for a real database.
    app.dependency_overrides[get_db_lazy] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
