"""A partial-column CSV must not overwrite the columns it omits (F-INV-7).

A Foodics export that ships only ``sku,name,stock_quantity`` used to reactivate
soft-deleted products, zero their price and uncategorise them, because the
importer set ``is_active`` / ``base_price`` / ``category_id`` from row defaults
whether or not the column existed. These pin the fix: an absent (or blank)
column leaves the field alone; a present column still updates it.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.category import Category
from app.models.modifier import Modifier, ModifierOption
from app.models.product import Product
from app.services.inventory import import_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-import-partial"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def existing_product(session_factory):
    """A soft-deleted, priced, categorised product — the shape the bug corrupted."""
    suffix = uuid.uuid4().hex[:10]
    sku = f"{MARKER}-{suffix}"
    async with session_factory() as db:
        category = Category(
            name=f"{MARKER} cat {suffix}",
            slug=f"{MARKER}-cat-{suffix}",
            reference=f"{MARKER}-catref-{suffix}",
        )
        db.add(category)
        await db.flush()
        product = Product(
            name="Original brownie",
            slug=f"{MARKER}-{suffix}",
            sku=sku,
            base_price=Decimal("55.00"),
            category_id=category.id,
            is_active=False,  # soft-deleted
            description="A rich brownie",
        )
        db.add(product)
        await db.commit()
        ids = (product.id, category.id, sku)
    yield ids
    product_id, category_id, _ = ids
    async with session_factory() as db:
        await db.execute(Product.__table__.delete().where(Product.id == product_id))
        await db.execute(Category.__table__.delete().where(Category.id == category_id))
        await db.commit()


async def test_partial_column_csv_leaves_absent_fields_untouched(
    session_factory, existing_product
):
    product_id, category_id, sku = existing_product
    # Only sku, name and stock_quantity — the classic breaking export.
    rows = [{"sku": sku, "name": "Renamed brownie", "stock_quantity": "7"}]

    async with session_factory() as db:
        result = await import_service.import_products(db, rows)
        await db.commit()

    assert result.updated == 1
    async with session_factory() as db:
        product = await db.get(Product, product_id)
        assert product.name == "Renamed brownie"  # present column: updated
        assert product.stock_quantity == 7  # present column: updated
        # Absent columns: untouched.
        assert product.is_active is False, "must not silently reactivate"
        assert product.base_price == Decimal("55.00"), "must not zero the price"
        assert product.category_id == category_id, "must not uncategorise"
        assert product.description == "A rich brownie"


async def test_blank_cells_are_treated_as_absent(session_factory, existing_product):
    product_id, category_id, sku = existing_product
    # The columns exist in the header but the cells are empty.
    rows = [
        {
            "sku": sku,
            "name": "Blanked brownie",
            "price": "",
            "is_active": "",
            "category_reference": "",
        }
    ]

    async with session_factory() as db:
        await import_service.import_products(db, rows)
        await db.commit()

    async with session_factory() as db:
        product = await db.get(Product, product_id)
        assert product.is_active is False
        assert product.base_price == Decimal("55.00")
        assert product.category_id == category_id


async def test_present_columns_still_update(session_factory, existing_product):
    product_id, _, sku = existing_product
    rows = [
        {
            "sku": sku,
            "name": "Reactivated brownie",
            "price": "60.00",
            "is_active": "true",
        }
    ]

    async with session_factory() as db:
        await import_service.import_products(db, rows)
        await db.commit()

    async with session_factory() as db:
        product = await db.get(Product, product_id)
        assert product.is_active is True
        assert product.base_price == Decimal("60.00")


@pytest.fixture
async def existing_option(session_factory):
    suffix = uuid.uuid4().hex[:10]
    sku = f"{MARKER}-opt-{suffix}"
    async with session_factory() as db:
        modifier = Modifier(
            reference=f"{MARKER}-mod-{suffix}",
            name="Filling",
            is_active=True,
        )
        db.add(modifier)
        await db.flush()
        option = ModifierOption(
            modifier_id=modifier.id,
            name="Ferrero",
            sku=sku,
            price=Decimal("12.00"),
            is_active=False,  # retired
        )
        db.add(option)
        await db.commit()
        ids = (option.id, modifier.id, modifier.reference, sku)
    yield ids
    option_id, modifier_id, _, _ = ids
    async with session_factory() as db:
        await db.execute(
            ModifierOption.__table__.delete().where(ModifierOption.id == option_id)
        )
        await db.execute(Modifier.__table__.delete().where(Modifier.id == modifier_id))
        await db.commit()


async def test_modifier_option_partial_column_csv_preserves_state(
    session_factory, existing_option
):
    option_id, _, modifier_ref, sku = existing_option
    # modifier_reference is required by the importer, but price/is_active absent.
    rows = [{"sku": sku, "name": "Ferrero XL", "modifier_reference": modifier_ref}]

    async with session_factory() as db:
        result = await import_service.import_modifier_options(db, rows)
        await db.commit()

    assert result.updated == 1
    async with session_factory() as db:
        option = await db.get(ModifierOption, option_id)
        assert option.name == "Ferrero XL"
        assert option.is_active is False, "must not silently reactivate"
        assert option.price == Decimal("12.00"), "must not zero the price"
