"""A new product is created under the shop's default tax group.

The admin create form has no tax field; a product with no group is charged no VAT
on the counter, which is how five products added in Sept 2026 sold at Sharjah.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.product import ProductCreate
from app.services.catalog import product_service


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()


async def test_default_tax_group_is_the_newest_live_default():
    db = MagicMock()
    group = uuid.uuid4()
    db.scalar = AsyncMock(return_value=group)
    assert await product_service.default_tax_group_id(db) == group
    sql = _sql(db.scalar.await_args.args[0])
    assert "tax_groups.is_default is true" in sql
    assert "tax_groups.is_active is true" in sql
    assert "tax_groups.deleted_at is null" in sql
    assert "order by tax_groups.created_at desc" in sql


async def test_create_stamps_the_default_tax_group():
    group = uuid.uuid4()
    added = []
    none_result = MagicMock()
    none_result.scalar_one_or_none.return_value = None
    db = MagicMock()
    db.execute = AsyncMock(return_value=none_result)
    db.flush = AsyncMock()
    db.add = added.append
    with (
        patch.object(
            product_service, "default_tax_group_id", AsyncMock(return_value=group)
        ),
        patch.object(product_service, "_enqueue_integrator_sync", AsyncMock()),
        patch.object(product_service.ProductResponse, "model_validate"),
    ):
        await product_service.create(
            db, ProductCreate(name="Classic Cake", slug="classic-cake")
        )
    assert added[0].tax_group_id == group
