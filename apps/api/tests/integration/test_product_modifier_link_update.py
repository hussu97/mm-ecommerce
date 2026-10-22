"""Editing a linked modifier's rules from the product page, against a real database.

The response is re-read after the write; with ``expire_on_commit=False`` a stale
identity map is how a write lands and the response describes the state before
it (see ``test_menu_group_crud``), so that is what this checks.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import Product
from app.schemas.product import ProductModifierLinkUpdate
from app.services.catalog import product_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]


@pytest.fixture
async def linked():
    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        tag = uuid.uuid4().hex[:10]
        product = Product(name="Brownie box", slug=f"pytest-box-{tag}")
        modifier = Modifier(reference=f"pytest-mod-{tag}", name="Flavours")
        db.add_all([product, modifier])
        await db.flush()
        db.add(
            ModifierOption(
                modifier_id=modifier.id, name="Fudge", sku=f"pytest-opt-{tag}"
            )
        )
        db.add(
            ProductModifier(
                product_id=product.id,
                modifier_id=modifier.id,
                minimum_options=3,
                maximum_options=3,
            )
        )
        await db.flush()
        yield db, product, modifier
        await db.rollback()
    await engine.dispose()


async def test_a_partial_update_changes_only_what_was_sent(linked):
    db, product, modifier = linked
    out = await product_service.update_modifier_link(
        db,
        product.slug,
        modifier.id,
        ProductModifierLinkUpdate(maximum_options=6, free_options=1),
    )
    (link,) = out.product_modifiers
    assert (link.minimum_options, link.maximum_options) == (3, 6)
    assert link.free_options == 1
    assert link.unique_options is False


async def test_a_minimum_above_the_maximum_is_refused(linked):
    db, product, modifier = linked
    with pytest.raises(BadRequestError):
        await product_service.update_modifier_link(
            db,
            product.slug,
            modifier.id,
            ProductModifierLinkUpdate(minimum_options=4),
        )


async def test_an_unlinked_modifier_is_not_found(linked):
    db, product, _ = linked
    with pytest.raises(NotFoundError):
        await product_service.update_modifier_link(
            db, product.slug, uuid.uuid4(), ProductModifierLinkUpdate(maximum_options=1)
        )
