"""A product slug change records a redirect, per locale (F-INV-12).

Categories kept their old URLs working on a rename; products did not, so every
indexed product URL 404'd the moment its slug changed. A product page is
/{locale}/{category}/{slug}, so the fix records one leaf redirect per locale.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.category import Category
from app.models.product import Product
from app.models.url_redirect import UrlRedirect
from app.schemas.product import ProductUpdate
from app.services import indexnow_service
from app.services.catalog import product_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-prodslug"


@pytest.fixture
async def session_factory():
    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    yield Session
    async with Session() as db:
        await db.execute(
            delete(UrlRedirect).where(UrlRedirect.from_path.like(f"/%/{MARKER}%"))
        )
        await db.execute(delete(Product).where(Product.slug.like(f"{MARKER}%")))
        await db.execute(delete(Category).where(Category.slug.like(f"{MARKER}%")))
        await db.commit()
    await engine.dispose()


async def test_product_slug_change_records_a_redirect_per_locale(session_factory):
    suffix = uuid.uuid4().hex[:8]
    cat_slug = f"{MARKER}-cat-{suffix}"
    old_slug = f"{MARKER}-old-{suffix}"
    new_slug = f"{MARKER}-new-{suffix}"

    async with session_factory() as db:
        category = Category(
            name="Brownies", slug=cat_slug, reference=f"{MARKER}-ref-{suffix}"
        )
        db.add(category)
        await db.flush()
        db.add(
            Product(
                name="Mix box", slug=old_slug, category_id=category.id, is_active=True
            )
        )
        await db.commit()

    async with session_factory() as db:
        await product_service.update(db, old_slug, ProductUpdate(slug=new_slug))
        await db.commit()

    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(UrlRedirect).where(
                        UrlRedirect.from_path.like(f"/%/{cat_slug}/{old_slug}")
                    )
                )
            )
            .scalars()
            .all()
        )

    by_from = {row.from_path: row for row in rows}
    for locale in indexnow_service.LOCALES:
        expected_from = f"/{locale}/{cat_slug}/{old_slug}"
        assert expected_from in by_from, f"missing redirect for {locale}"
        row = by_from[expected_from]
        assert row.to_path == f"/{locale}/{cat_slug}/{new_slug}"
        assert row.is_prefix is False, "a product is a leaf URL"
        assert row.is_active is True
        assert row.source == "product_rename"


async def test_no_redirect_when_the_slug_is_unchanged(session_factory):
    suffix = uuid.uuid4().hex[:8]
    cat_slug = f"{MARKER}-cat2-{suffix}"
    slug = f"{MARKER}-keep-{suffix}"

    async with session_factory() as db:
        category = Category(
            name="Cookies", slug=cat_slug, reference=f"{MARKER}-ref2-{suffix}"
        )
        db.add(category)
        await db.flush()
        db.add(Product(name="Cookie", slug=slug, category_id=category.id))
        await db.commit()

    async with session_factory() as db:
        # A non-slug edit must not manufacture a redirect.
        await product_service.update(db, slug, ProductUpdate(name="Cookie renamed"))
        await db.commit()

    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(UrlRedirect).where(
                        UrlRedirect.from_path.like(f"/%/{cat_slug}/%")
                    )
                )
            )
            .scalars()
            .all()
        )
    assert rows == []
