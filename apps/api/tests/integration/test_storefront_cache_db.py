"""
Which committed writes purge the storefront, and which moments of the clock do.

Against a real database because the property is about SQLAlchemy's session
events: a unit-of-work flush, a bulk ORM statement (and whether it matched a
row), a commit, a rollback.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.branch import Branch
from app.models.menu import BranchProduct
from app.models.product import Product
from app.models.promo_code import PromoCode
from app.models.user import User
from app.services import storefront_cache
from app.services.pos import counter_bundle_cache

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-sfcache"


@pytest.fixture
async def Session():
    engine = create_async_engine(DATABASE_URL)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def purged(monkeypatch):
    calls: list[set[str]] = []
    monkeypatch.setattr(storefront_cache, "purge", lambda tags: calls.append(set(tags)))
    return calls


def _product() -> Product:
    tag = uuid.uuid4().hex[:10]
    return Product(
        name=f"{MARKER} {tag}", slug=f"{MARKER}-{tag}", base_price=Decimal("5.00")
    )


async def _cleanup(Session, product_id):
    async with Session() as db:
        await db.execute(delete(Product).where(Product.id == product_id))
        await db.commit()


async def test_a_committed_catalogue_write_purges_the_catalogue(Session, purged):
    async with Session() as db:
        product = _product()
        db.add(product)
        await db.commit()
    assert purged == [{"catalogue"}]

    async with Session() as db:
        row = await db.get(Product, product.id)
        row.base_price = Decimal("6.00")
        await db.commit()
    assert purged[-1] == {"catalogue"}
    await _cleanup(Session, product.id)


async def test_a_rollback_purges_nothing(Session, purged):
    async with Session() as db:
        db.add(_product())
        await db.flush()
        await db.rollback()
        # The session is reused for a commit with nothing storefront-shaped in it.
        await db.commit()
    assert purged == []


async def test_an_unchanged_assignment_is_not_a_change(Session, purged):
    async with Session() as db:
        product = _product()
        db.add(product)
        await db.commit()
    purged.clear()
    async with Session() as db:
        row = await db.get(Product, product.id)
        row.base_price = row.base_price  # dirty, but nothing moved
        await db.commit()
    assert purged == []
    await _cleanup(Session, product.id)


async def test_a_bulk_update_purges_only_when_it_matched_a_row(Session, purged):
    async with Session() as db:
        product = _product()
        db.add(product)
        await db.commit()
    purged.clear()

    # `promote`'s per-line stock decrement: guarded on a stock product, so in a
    # shop with none it matches nothing and must not purge every time.
    async with Session() as db:
        result = await db.execute(
            update(Product)
            .where(Product.id == product.id, Product.is_stock_product.is_(True))
            .values(stock_quantity=Product.stock_quantity - 1)
            .execution_options(synchronize_session=False)
        )
        assert result.rowcount == 0
        await db.commit()
    assert purged == []

    async with Session() as db:
        result = await db.execute(
            update(Product)
            .where(Product.id == product.id)
            .values(display_order=7)
            .execution_options(synchronize_session=False)
        )
        assert result.rowcount == 1
        await db.commit()
    assert purged == [{"catalogue"}]
    await _cleanup(Session, product.id)


async def test_running_the_bulk_statement_still_tells_the_counter_bundle(
    Session, purged, monkeypatch
):
    # This module runs the statement itself to read its rowcount; the counter
    # bundle's own `do_orm_execute` listener must still see it.
    bumps: list[bool] = []
    monkeypatch.setattr(counter_bundle_cache, "bump", lambda: bumps.append(True))
    async with Session() as db:
        await db.execute(
            update(Product)
            .where(Product.id == uuid.uuid4())
            .values(display_order=1)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    assert bumps == [True]


async def test_a_promo_use_purges_the_advertised_coupon(Session, purged):
    code = f"SFC{uuid.uuid4().hex[:8].upper()}"
    async with Session() as db:
        db.add(
            PromoCode(
                code=code, discount_type="percentage", discount_value=Decimal("10")
            )
        )
        await db.commit()
    assert purged == [{"promo"}]
    async with Session() as db:
        # The checkout's increment is a bulk UPDATE, not a flush.
        await db.execute(
            update(PromoCode)
            .where(PromoCode.code == code)
            .values(current_uses=PromoCode.current_uses + 1)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    assert purged[-1] == {"promo"}
    async with Session() as db:
        await db.execute(delete(PromoCode).where(PromoCode.code == code))
        await db.commit()


async def test_writes_elsewhere_purge_nothing(Session, purged):
    async with Session() as db:
        await db.execute(
            update(User)
            .where(User.id == uuid.uuid4())
            .values(phone="x")
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    assert purged == []


# ─── The clock ────────────────────────────────────────────────────────────────


@pytest.fixture
async def rolled_back():
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
    await engine.dispose()


async def _stockout(db, until: datetime) -> None:
    tag = uuid.uuid4().hex[:10]
    branch = Branch(name=f"{MARKER} {tag}", reference=f"SF{tag}")
    product = _product()
    db.add_all([branch, product])
    await db.flush()
    db.add(
        BranchProduct(
            branch_id=branch.id,
            product_id=product.id,
            is_in_stock=False,
            out_of_stock_until=until,
            unavailable_source="staff",
        )
    )
    await db.flush()


# Far enough out that nothing else in a shared test database sits in the window.
FAR = datetime(2099, 1, 1, 12, 0, tzinfo=timezone.utc)


async def test_a_lapsing_stockout_is_a_catalogue_boundary(rolled_back):
    db = rolled_back
    await _stockout(db, FAR)
    window = (FAR - timedelta(seconds=30), FAR)
    assert await storefront_cache.crossed_boundaries(db, *window) == {"catalogue"}
    # The moment counts once: not in the window that starts on it.
    after = await storefront_cache.crossed_boundaries(
        db, FAR, FAR + timedelta(seconds=30)
    )
    assert "catalogue" not in after


async def test_a_coupon_window_is_a_promo_boundary(rolled_back):
    db = rolled_back
    db.add(
        PromoCode(
            code=f"SFC{uuid.uuid4().hex[:8].upper()}",
            discount_type="percentage",
            discount_value=Decimal("10"),
            valid_from=FAR,
            valid_until=FAR + timedelta(days=1),
        )
    )
    await db.flush()
    opens = await storefront_cache.crossed_boundaries(
        db, FAR - timedelta(seconds=30), FAR
    )
    assert "promo" in opens
    # Live through `valid_until` itself; gone in the window just after it.
    end = FAR + timedelta(days=1)
    closes = await storefront_cache.crossed_boundaries(
        db, end, end + timedelta(seconds=30)
    )
    assert "promo" in closes


async def test_a_redis_entry_never_outlives_the_next_stockout_expiry(rolled_back):
    db = rolled_back
    soon = datetime.now(timezone.utc) + timedelta(seconds=40)
    await _stockout(db, soon)
    ttl = await storefront_cache.seconds_to_next_catalogue_boundary(db, 300)
    assert 1 <= ttl <= 42
