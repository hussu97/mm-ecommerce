"""
The database refuses a basket with no owner.

`ck_carts_has_identity`, asserted where it matters rather than by reading the
migration. Production carried one such row from 8 March 2026 until migration
117, and four different customers' items accumulated in it — it was matched by
`session_id IS NULL`, which is what a lookup for a shopper carrying no session
id compiles to, so it behaved as a single basket shared by everyone whose
browser had lost its session id.

The service now refuses to create one (`require_identity`) and refuses to find
one (`identity_clause`). This is the third lock, and it is the only one that
still holds for a row inserted by something that is not this service — a
console, a restored dump, a future migration.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.cart import Cart

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


@pytest.mark.asyncio
async def test_a_cart_with_no_identity_is_refused(sessionmaker):
    async with sessionmaker() as db:
        with pytest.raises(IntegrityError):
            db.add(Cart())
            await db.flush()
        await db.rollback()


@pytest.mark.asyncio
async def test_a_blank_session_id_is_refused(sessionmaker):
    """
    Not a value this codebase writes, but it is what a header that arrives
    present-and-empty would become — and `WHERE session_id = ''` would then
    collect session-less shoppers into one basket exactly as `IS NULL` did.
    """
    async with sessionmaker() as db:
        with pytest.raises(IntegrityError):
            db.add(Cart(session_id=""))
            await db.flush()
        await db.rollback()


@pytest.mark.asyncio
async def test_a_guest_session_is_still_allowed(sessionmaker):
    session_id = f"test-{uuid.uuid4()}"
    async with sessionmaker() as db:
        db.add(Cart(session_id=session_id))
        await db.commit()

        await db.execute(
            text("DELETE FROM carts WHERE session_id = :s"), {"s": session_id}
        )
        await db.commit()
