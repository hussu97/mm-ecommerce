"""
Every Postgres connection says which process opened it.

`pg_stat_activity.application_name` was empty for all of them, so the storefront
worker, its scheduler sibling and the register could not be told apart on the
database. Each entrypoint's `configure_observability(service=...)` now names its
connections with the same service name it tags Sentry with.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import database

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")


@pytest.fixture
def restore_name():
    before = database._application_name
    yield
    database.set_application_name(before)


def test_configure_observability_names_the_process(restore_name):
    from app.app_setup import configure_observability

    configure_observability(service="mm-scheduler")
    assert database._application_name == "mm-scheduler"


def test_the_stamp_rides_server_settings_and_keeps_the_idle_timeout(restore_name):
    database.set_application_name("mm-pos-api")
    connect_args = database._connect_args("postgresql+asyncpg://u:p@h/db")
    cparams = dict(connect_args)
    database.engine.sync_engine.dialect.dispatch.do_connect(
        database.engine.sync_engine.dialect, None, [], cparams
    )
    assert cparams["server_settings"] == {
        "idle_in_transaction_session_timeout": "60000",
        "application_name": "mm-pos-api",
    }
    # The engine-wide dict is replaced, not mutated in place.
    assert "application_name" not in connect_args["server_settings"]


def test_a_non_asyncpg_url_is_left_alone(restore_name):
    cparams: dict = {}
    database.engine.sync_engine.dialect.dispatch.do_connect(
        database.engine.sync_engine.dialect, None, [], cparams
    )
    assert cparams == {}


def test_the_name_is_truncated_to_what_postgres_keeps(restore_name):
    database.set_application_name("x" * 100)
    assert database._application_name == "x" * 63


@pytest.mark.asyncio
@pytest.mark.skipif(
    not DATABASE_URL or "asyncpg" not in DATABASE_URL,
    reason="needs an asyncpg database — set TEST_DATABASE_URL",
)
async def test_postgres_sees_the_name(restore_name):
    probe = create_async_engine(
        DATABASE_URL, connect_args=database._connect_args(DATABASE_URL)
    )
    database._name_connections(probe)
    database.set_application_name("mm-test-probe")
    try:
        async with probe.connect() as conn:
            name = await conn.scalar(text("SHOW application_name"))
            idle = await conn.scalar(text("SHOW idle_in_transaction_session_timeout"))
    finally:
        await probe.dispose()
    assert name == "mm-test-probe"
    assert idle == "1min"
