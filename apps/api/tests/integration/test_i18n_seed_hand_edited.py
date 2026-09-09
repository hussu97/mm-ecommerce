"""The i18n seeder keeps a console edit but still reverts source drift (F-ADM-5).

The seeder runs on every boot and used to overwrite any row whose value differed
from the source constant — reverting every Translations-console edit. It now
leaves a row alone once `hand_edited_at` is stamped (the console stamps it on
save), while a row nobody hand-edited is still restored to source.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import utcnow
from app.models.language import UiTranslation
from scripts import seed_i18n

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

# Two known source strings (see ALL_TRANSLATIONS): nav.home = "Home", nav.menu = "Menu".
HAND = ("en", "nav", "home", "Home")
DRIFT = ("en", "nav", "menu", "Menu")


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _dispose_scheduler_pool():
    """`seed()` takes its advisory lock on the module-level `scheduler_engine`,
    whose pooled connection would otherwise outlive this test's event loop and
    trip asyncpg's teardown in a later advisory-lock test. Dispose it after each
    test so the loop closes clean — same guard `test_daily_sales_idempotency`
    uses."""
    yield
    from app.core.database import scheduler_engine

    await scheduler_engine.dispose()


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """Keep the seeder off Redis so the test is hermetic (force=True skips the
    read; stub the write and the cache drop)."""

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(seed_i18n, "cache_set", _noop)
    monkeypatch.setattr(seed_i18n, "cache_get", _noop)
    from app.services import i18n_service

    monkeypatch.setattr(i18n_service, "invalidate_translations", _noop)


async def _row(db, spec):
    locale, namespace, key, _ = spec
    return (
        await db.execute(
            select(UiTranslation).where(
                UiTranslation.locale == locale,
                UiTranslation.namespace == namespace,
                UiTranslation.key == key,
            )
        )
    ).scalar_one()


async def test_a_hand_edited_string_survives_a_reseed_and_drift_does_not(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)

    # Start from a clean source seed so both rows exist at their source value.
    async with Session() as db:
        await seed_i18n.seed(db, force=True)

    # One row is edited in the console (value changed + stamped); the other drifts
    # away from source with no stamp (as a stray write or an older code path would).
    async with Session() as db:
        hand = await _row(db, HAND)
        hand.value = "Welcome home"
        hand.hand_edited_at = utcnow()
        drift = await _row(db, DRIFT)
        drift.value = "Drifted menu"
        drift.hand_edited_at = None
        await db.commit()

    # The next boot's seed.
    async with Session() as db:
        await seed_i18n.seed(db, force=True)

    async with Session() as db:
        assert (await _row(db, HAND)).value == "Welcome home"  # kept
        assert (await _row(db, DRIFT)).value == "Menu"  # reverted to source

    # Leave the table as the source seed found it.
    async with Session() as db:
        hand = await _row(db, HAND)
        hand.value = "Home"
        hand.hand_edited_at = None
        await db.commit()
