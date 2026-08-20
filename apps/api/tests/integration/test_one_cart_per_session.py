"""
One guest, one basket — even when two requests ask at once.

This is the bug that took the storefront down for six customers in the week of
20 August 2026, and it needs a real database because it is a race and a unique
index: a mocked session cannot tell you what Postgres does when two INSERTs
collide, and that collision is the entire fix.

**What happened.** `carts` had a unique index on `user_id` and nothing on
`session_id`, and every lookup for a guest's basket was `WHERE session_id = ?`
followed by `scalar_one_or_none()` — a call that asserts a uniqueness the table
did not have. A double tap on a phone, or a retry after a slow response, and two
requests both found no cart and both made one. From then on **every** read of
that basket raised `MultipleResultsFound`: adding to the cart and loading the
cart both answered 500 until the rows were cleared.

One of those customers is on a session recording. They dead-clicked an upsell,
then watched "Loading your cart…" spin, with the item count on the bag icon
falling from 1 to nothing.

Two halves are asserted here, and neither is sufficient alone:

* the index makes the duplicate impossible, and
* the lookup no longer raises when one exists anyway — which is what a database
  restored from a dump taken before the index still carries.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.cart import Cart
from app.services import cart_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)


@pytest.fixture
async def sessionmaker():
    engine = create_async_engine(DATABASE_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _cart_count(db, session_id: str) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(Cart)
                .where(Cart.session_id == session_id)
            )
        ).scalar()
        or 0
    )


@pytest.mark.asyncio
async def test_the_database_refuses_a_second_cart_for_one_session(sessionmaker):
    """
    `uq_carts_session_id`, asserted where it matters rather than by reading the
    migration. Without it every other guarantee here is a convention.
    """
    session_id = f"test-{uuid.uuid4()}"
    async with sessionmaker() as db:
        db.add(Cart(session_id=session_id))
        await db.commit()

        with pytest.raises(IntegrityError):
            db.add(Cart(session_id=session_id))
            await db.flush()
        await db.rollback()

        await db.execute(
            text("DELETE FROM carts WHERE session_id = :s"), {"s": session_id}
        )
        await db.commit()


@pytest.mark.asyncio
async def test_two_simultaneous_first_items_share_one_cart(sessionmaker):
    """
    The race itself, run for real: two requests, neither seeing the other's
    cart, both calling `get_or_create_cart`.

    Before the fix this produced two rows and every later read raised. It now
    produces one row and both callers are handed it.
    """
    session_id = f"test-{uuid.uuid4()}"

    async def first_item():
        async with sessionmaker() as db:
            cart = await cart_service.get_or_create_cart(db, None, session_id)
            await db.commit()
            return cart.id

    a, b = await asyncio.gather(first_item(), first_item())

    async with sessionmaker() as db:
        assert await _cart_count(db, session_id) == 1, "the guest got two baskets"
        assert a == b, "the two requests are holding different carts"

        # And the read that used to raise now answers.
        found = await cart_service.cart_for_identity(db, None, session_id)
        assert found is not None and found.id == a

        await db.execute(
            text("DELETE FROM carts WHERE session_id = :s"), {"s": session_id}
        )
        await db.commit()


@pytest.mark.asyncio
async def test_a_duplicate_that_already_exists_does_not_refuse_the_customer(
    sessionmaker,
):
    """
    The half the index cannot cover: a database restored from a dump taken
    before it, still carrying yesterday's duplicates.

    The index is dropped for the length of this test so two rows can be made at
    all — which is the only way to assert that `find_cart` answers instead of
    raising, and it is worth asserting because that is what stands between a
    restored backup and a storefront that refuses every guest.
    """
    session_id = f"test-{uuid.uuid4()}"
    async with sessionmaker() as db:
        await db.execute(text("DROP INDEX IF EXISTS uq_carts_session_id"))
        db.add(Cart(session_id=session_id))
        db.add(Cart(session_id=session_id))
        await db.commit()

        try:
            assert await _cart_count(db, session_id) == 2

            # The old code raised MultipleResultsFound here, which is a 500 on
            # both "add to cart" and "show me my cart".
            found = await cart_service.cart_for_identity(db, None, session_id)
            assert found is not None

            # And deterministically the same one every time, so two screens do
            # not disagree about what is in the basket.
            again = await cart_service.cart_for_identity(db, None, session_id)
            assert again.id == found.id
        finally:
            await db.execute(
                text("DELETE FROM carts WHERE session_id = :s"), {"s": session_id}
            )
            await db.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_carts_session_id "
                    "ON carts (session_id) WHERE session_id IS NOT NULL"
                )
            )
            await db.commit()
